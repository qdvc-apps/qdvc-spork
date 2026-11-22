# A `??` fallback written as an unlabeled tuple silently drops the tuple's labels

- **Date encountered:** 2026-11-22
- **Reported by:** Claude (Anthropic, Claude Opus 5.5)
- **Project visibility:** Public
- **Project:** qdvc-spork
- **Platform:** Swift, SwiftUI and AppKit (macOS)
- **Versions:** Swift package with `swift-tools-version:5.9` (Swift 5 language mode), macOS 14 deployment target, built with `scripts/build-app.sh` on the user's Mac. Exact Xcode/Swift compiler version unknown (not reported). The behaviour comes from Swift's type checker, not from any framework, so it is not macOS-specific.
- **Log link:** Pending: qdvc-spork, ai-assistance-logs/2026-11-22-claude.md, UR.2
- **Status:** Fix not yet confirmed (the corrected code hasn't been rebuilt yet)
- **Related entry:** "`.help` with a `??` fallback picks the localised-string overload" (same family: the type of the `??` fallback steers inference somewhere you didn't intend; a different symptom and fix)

## Symptom

A function returns an optional labeled tuple, and the call site falls back with `??` and a tuple literal. Reading a label from the result fails to compile:

```
AppModel.swift:146:79: error: value of tuple type '([String : String], [String])' has no member 'errors'
    Census.summarize(path: path, runner: runner, fetchErrors: ctx.errors, offline: ctx.offline)
AppModel.swift:146:100: error: value of tuple type '([String : String], [String])' has no member 'offline'
```

`ctx` came from:

```swift
func context(for repo: String) -> (errors: [String: String], offline: [String]) { … }
let ctx = lastFetch?.context(for: path) ?? ([:], [])
```

The confusing part: the very same expression compiled without complaint in another file, where it was the body of a computed property declared as `-> (errors: [String: String], offline: [String])`.

## Cause

`??` is generic: `(T?, @autoclosure () throws -> T) -> T`. Swift implicitly converts between a labeled tuple type and the unlabeled tuple type with the same element types, so the solver may choose either one for `T`. With no type annotation on `ctx`, it settled on the unlabeled type written in the literal, `([String: String], [String])`. The left side's `(errors:offline:)` payload was converted to match, and the labels were gone. The error message shows that unlabeled type, so this much is observed, not guessed.

Where the expression is the body of a property with a declared labeled return type, that return type takes part in inference and `T` comes out labeled, so it compiles. The bug only shows up when the result goes into a `let` with no annotation. The explanation of why the solver prefers the literal's type is reasoned from Swift's tuple-conversion rules; I didn't check it against the compiler source.

## Fix

Give the tuple a name and use it on both sides, so nothing is left to infer:

```swift
typealias FetchContext = (errors: [String: String], offline: [String])
let noFetchContext: FetchContext = (errors: [:], offline: [])

func context(for repo: String) -> FetchContext { … }
let ctx: FetchContext = lastFetch?.context(for: path) ?? noFetchContext
```

Either half alone would also work: annotate `let ctx: FetchContext`, or write the fallback with labels, `?? (errors: [:], offline: [])`. Doing both means a later edit to one side can't bring the problem back.

The code was written without a compiler available (Linux sandbox, no Xcode), so it reached the user unbuilt; a compiler would have caught this on the first build.

## Watch for

- Any `optionalLabeledTuple ?? (a, b)` whose result goes into a `let` or `var` without a type annotation. The same conversion can happen with a ternary (`cond ? labeled : (a, b)`) and other generic functions whose two arguments share one type parameter.
- "Compiles here, fails there" for the same expression: check whether one site has a declared type that steers inference.
- For values passed between files, a small `struct` is sturdier than a labeled tuple, which can always lose its labels through an implicit conversion.

## Minimal example

```swift
func lookup() -> (name: String, count: Int)? { nil }

let a = lookup() ?? ("", 0)
print(a.name)          // error: value of tuple type '(String, Int)' has no member 'name'

let b: (name: String, count: Int) = lookup() ?? ("", 0)   // OK: annotation fixes T
let c = lookup() ?? (name: "", count: 0)                  // OK: labeled literal
```
