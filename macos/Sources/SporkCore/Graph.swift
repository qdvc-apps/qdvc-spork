import Foundation

/// One row of the commit graph: the node's lane and the line segments to draw
/// in the top half (into the node) and bottom half (out of the node).
public struct GraphRow: Hashable, Sendable {
    public struct Segment: Hashable, Sendable {
        public let from: Int
        public let to: Int
        public let color: Int
        public init(_ from: Int, _ to: Int, _ color: Int) {
            self.from = from
            self.to = to
            self.color = color
        }
    }
    public var node: Int
    public var color: Int
    public var top: [Segment] = []
    public var bottom: [Segment] = []
    public var width: Int = 1

    public init(node: Int, color: Int) {
        self.node = node
        self.color = color
    }
}

/// Incremental lane layout; feed commits in `git log --topo-order` order.
/// Same algorithm as the GTK edition's sporkapp/core/graph.py.
public final class GraphBuilder {
    var lanes: [String?] = []
    var colors: [Int] = []
    var nextColor = 0

    public init() {}

    func newColor() -> Int {
        defer { nextColor += 1 }
        return nextColor
    }

    func freeLane() -> Int {
        if let i = lanes.firstIndex(where: { $0 == nil }) { return i }
        lanes.append(nil)
        colors.append(0)
        return lanes.count - 1
    }

    public func add(_ commits: [Commit]) -> [GraphRow] { commits.map(addOne) }

    func addOne(_ c: Commit) -> GraphRow {
        let incoming = lanes.indices.filter { lanes[$0] == c.sha }
        let node: Int
        if let first = incoming.first {
            node = first
        } else {
            node = freeLane()
            colors[node] = newColor()
        }
        let color = colors[node]
        let before = lanes
        let beforeColors = colors
        var row = GraphRow(node: node, color: color)
        for (i, v) in before.enumerated() {
            guard let v else { continue }
            row.top.append(.init(i, v == c.sha ? node : i, beforeColors[i]))
        }
        for i in incoming { lanes[i] = nil }
        lanes[node] = nil

        for (pi, p) in c.parents.enumerated() {
            let existing = lanes.firstIndex(where: { $0 == p })
            if pi == 0, let ex = existing, ex > node {
                lanes[ex] = nil
                lanes[node] = p
                colors[node] = color
                row.bottom.append(.init(node, node, color))
                row.bottom.append(.init(ex, node, colors[ex]))
                continue
            }
            if let ex = existing {
                row.bottom.append(.init(node, ex, colors[ex]))
                continue
            }
            let lane: Int
            if pi == 0 {
                lane = node
                colors[lane] = color
            } else {
                lane = freeLane()
                colors[lane] = newColor()
            }
            lanes[lane] = p
            row.bottom.append(.init(node, lane, colors[lane]))
        }
        for (i, v) in before.enumerated() {
            if let v, v != c.sha, i < lanes.count, lanes[i] == v {
                row.bottom.append(.init(i, i, colors[i]))
            }
        }
        while let last = lanes.last, last == nil {
            lanes.removeLast()
            colors.removeLast()
        }
        let used = [row.node] + (row.top + row.bottom).flatMap { [$0.from, $0.to] }
        row.width = (used.max() ?? 0) + 1
        return row
    }
}
