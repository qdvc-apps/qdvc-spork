import AppKit
import SporkCore
import SwiftUI

extension Binding where Value: Equatable {
    /// A binding that ignores writes of the value it already holds. AppKit-backed
    /// controls write unchanged values back, which can loop observation into a
    /// freeze (Cursed Knowledge).
    init(guarded get: @escaping () -> Value, set: @escaping (Value) -> Void) {
        self.init(get: get, set: { new in if new != get() { set(new) } })
    }
}

/// Two panes with a draggable divider. Used instead of HSplitView, which ignores
/// SwiftUI's safe area and slides under the toolbar (Cursed Knowledge).
struct ResizableHSplit<Leading: View, Trailing: View>: View {
    @Binding var leadingWidth: Double
    var minLeading: Double = 200
    var minTrailing: Double = 320
    @ViewBuilder var leading: () -> Leading
    @ViewBuilder var trailing: () -> Trailing
    @State private var dragStart: Double?

    var body: some View {
        GeometryReader { geo in
            let maxLeading = max(minLeading, geo.size.width - minTrailing)
            let width = min(max(leadingWidth, minLeading), maxLeading)
            HStack(spacing: 0) {
                leading().frame(width: width)
                Rectangle()
                    .fill(Color(nsColor: .separatorColor))
                    .frame(width: 1)
                    .overlay(
                        Color.clear
                            .frame(width: 9)
                            .contentShape(Rectangle())
                            .onHover { inside in
                                if inside { NSCursor.resizeLeftRight.push() } else { NSCursor.pop() }
                            }
                            .gesture(
                                DragGesture(minimumDistance: 1, coordinateSpace: .global)
                                    .onChanged { v in
                                        let start = dragStart ?? width
                                        if dragStart == nil { dragStart = width }
                                        leadingWidth = min(max(start + v.translation.width, minLeading), maxLeading)
                                    }
                                    .onEnded { _ in dragStart = nil }
                            )
                    )
                trailing().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }
}

struct RefPill: View {
    let label: RefLabel

    var body: some View {
        Text(label.name)
            .font(.caption2.weight(.semibold))
            .lineLimit(1)
            .truncationMode(.middle)
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .foregroundStyle(foreground)
            .background(background, in: RoundedRectangle(cornerRadius: 4, style: .continuous))
            .help(tooltip)
    }

    var tooltip: String {
        switch label.kind {
        case .head: return "Current branch: " + label.name
        case .branch: return "Branch: " + label.name
        case .remote: return "Remote branch: " + label.name
        case .tag: return "Tag: " + label.name
        case .detached: return "Detached HEAD"
        }
    }

    var foreground: Color {
        switch label.kind {
        case .head: return .white
        case .branch: return .accentColor
        case .remote: return .secondary
        case .tag: return .orange
        case .detached: return .white
        }
    }

    var background: Color {
        switch label.kind {
        case .head: return .accentColor
        case .branch: return .accentColor.opacity(0.15)
        case .remote: return .secondary.opacity(0.15)
        case .tag: return .orange.opacity(0.18)
        case .detached: return .red
        }
    }
}

struct LevelIcon: View {
    let level: Level?

    var body: some View {
        Image(systemName: symbol)
            .foregroundStyle(color)
            .help(help)
    }

    var symbol: String {
        switch level {
        case .ok: return "checkmark.circle.fill"
        case .attention: return "exclamationmark.triangle.fill"
        case .unknown: return "questionmark.circle"
        case .missing: return "xmark.octagon.fill"
        case nil: return "circle.dotted"
        }
    }

    var color: Color {
        switch level {
        case .ok: return .green
        case .attention: return .orange
        case .unknown, nil: return .secondary
        case .missing: return .red
        }
    }

    var help: String {
        switch level {
        case .ok: return "Clean and in sync"
        case .attention: return "Needs attention"
        case .unknown: return "Some remotes couldn't be checked"
        case .missing: return "Not found"
        case nil: return "Checking…"
        }
    }
}

struct StatusDot: View {
    let level: Level?
    var body: some View {
        Circle().fill(LevelIcon(level: level).color).frame(width: 8, height: 8)
    }
}

struct ChangeLetter: View {
    let code: String

    var body: some View {
        Text(code)
            .font(.system(.caption, design: .monospaced).weight(.bold))
            .foregroundStyle(color)
            .frame(width: 16)
            .help(help)
    }

    var color: Color {
        switch code {
        case "A", "?": return .green
        case "D": return .red
        case "R", "C": return .purple
        case "U": return .orange
        default: return .accentColor
        }
    }

    var help: String {
        switch code {
        case "A": return "Added"
        case "M": return "Modified"
        case "D": return "Deleted"
        case "R": return "Renamed"
        case "C": return "Copied"
        case "T": return "Type changed"
        case "U": return "Conflicted"
        case "?": return "Untracked"
        default: return code
        }
    }
}

/// A transient message at the bottom of the window.
struct ToastView: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.callout)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(.regularMaterial, in: Capsule())
            .shadow(radius: 6, y: 2)
            .padding(.bottom, 18)
            .transition(.move(edge: .bottom).combined(with: .opacity))
    }
}

func emptyState(_ title: String, _ symbol: String, _ description: String = "") -> some View {
    ContentUnavailableView {
        Label(title, systemImage: symbol)
    } description: {
        if !description.isEmpty { Text(description) }
    }
}
