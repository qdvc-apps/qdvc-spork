import Foundation

/// The askpass helper git (GIT_ASKPASS) and ssh (SSH_ASKPASS) run when they
/// need a secret. It shows a native dialog through osascript and prints the
/// answer; exit status 1 means the user cancelled.
public enum Askpass {
    public static let script = #"""
    #!/bin/sh
    # Written by QDVC Spork. Asks for git/ssh passwords in a dialog.
    SPORK_PROMPT="${1:-Password:}"
    case "$SPORK_PROMPT" in
        *"(yes/no"*) SPORK_MODE=confirm ;;
        *[Uu]sername*) SPORK_MODE=text ;;
        *) SPORK_MODE=secret ;;
    esac
    export SPORK_PROMPT SPORK_MODE
    exec /usr/bin/osascript -l JavaScript - <<'JS'
    ObjC.import('stdlib');
    var app = Application.currentApplication();
    app.includeStandardAdditions = true;
    var env = $.NSProcessInfo.processInfo.environment;
    var prompt = ObjC.unwrap(env.objectForKey('SPORK_PROMPT'));
    var mode = ObjC.unwrap(env.objectForKey('SPORK_MODE'));
    var result;
    try {
        app.activate();
        if (mode === 'confirm') {
            var r = app.displayDialog(prompt, {withTitle: 'QDVC Spork', buttons: ['No', 'Yes'],
                                               defaultButton: 'Yes', withIcon: 'caution'});
            result = r.buttonReturned === 'Yes' ? 'yes' : 'no';
        } else {
            var r2 = app.displayDialog(prompt, {withTitle: 'QDVC Spork', defaultAnswer: '',
                                                hiddenAnswer: mode === 'secret',
                                                buttons: ['Cancel', 'OK'], defaultButton: 'OK',
                                                cancelButton: 'Cancel', withIcon: 'note'});
            result = r2.textReturned;
        }
    } catch (e) {
        $.exit(1);
    }
    result;
    JS
    """#

    /// Writes the helper to Spork's support folder (if changed) and returns its path.
    public static func install(in folder: URL) throws -> String {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = folder.appendingPathComponent("spork-askpass")
        let current = try? String(contentsOf: url, encoding: .utf8)
        if current != script {
            try script.write(to: url, atomically: true, encoding: .utf8)
        }
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
        return url.path
    }
}
