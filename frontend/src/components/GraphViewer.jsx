import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getGraph } from "../api";

const WIDTH = 860;
const HEIGHT = 580;
const NODE_W = 140;
const NODE_H = 34;

// Force-directed layout, hand-rolled - the graph is 9 nodes / 13 edges, far
// too small to justify a physics library. Three forces: nodes repel each
// other (so they spread out instead of clustering), edges pull their two
// endpoints toward an ideal length (so connected tables end up near each
// other), and everything is pulled gently toward center (so the graph
// doesn't drift off-canvas). `alpha` decays each tick like d3-force's
// convention - the simulation runs hot on load or on drag, then cools to a
// stable rest instead of jittering forever.
const REPULSION = 18000;
const SPRING_LENGTH = 190;
const SPRING_STRENGTH = 0.035;
const CENTER_STRENGTH = 0.008;
const DAMPING = 0.82;
const ALPHA_DECAY = 0.985;
// Point-repulsion alone isn't enough - two node *centers* can be "far
// enough" by the physics while their 140px-wide boxes still overlap. This
// is the minimum center-to-center distance actually enforced, sized to the
// node's bounding half-diagonal plus a visible gap.
const COLLISION_DIST = Math.hypot(NODE_W / 2, NODE_H / 2) + 46;

function seedPositions(nodes) {
  const ordered = [...nodes].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "dimension" ? -1 : 1;
    return a.id.localeCompare(b.id);
  });
  const n = ordered.length;
  const positions = {};
  ordered.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / n - Math.PI / 2;
    positions[node.id] = {
      x: WIDTH / 2 + 200 * Math.cos(angle),
      y: HEIGHT / 2 + 200 * Math.sin(angle),
    };
  });
  return positions;
}

// One physics tick - pure, so it can be run synchronously in a tight loop
// to pre-solve the layout before the first paint, and reused unchanged in
// the animated per-frame loop. Mutates pos/vel in place.
function simulationStep(nodes, edges, pos, vel, alpha, draggedId) {
  const forces = Object.fromEntries(nodes.map((n) => [n.id, { fx: 0, fy: 0 }]));

  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i].id;
      const b = nodes[j].id;
      let dx = pos[a].x - pos[b].x;
      let dy = pos[a].y - pos[b].y;
      let distSq = dx * dx + dy * dy;
      if (distSq < 1) {
        dx = Math.random() - 0.5;
        dy = Math.random() - 0.5;
        distSq = 1;
      }
      const dist = Math.sqrt(distSq);
      const force = (REPULSION / distSq) * alpha;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      forces[a].fx += fx;
      forces[a].fy += fy;
      forces[b].fx -= fx;
      forces[b].fy -= fy;
    }
  }

  for (const e of edges) {
    const pa = pos[e.source];
    const pb = pos[e.target];
    if (!pa || !pb) continue;
    const dx = pb.x - pa.x;
    const dy = pb.y - pa.y;
    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
    const force = SPRING_STRENGTH * (dist - SPRING_LENGTH) * alpha;
    const fx = (dx / dist) * force;
    const fy = (dy / dist) * force;
    forces[e.source].fx += fx;
    forces[e.source].fy += fy;
    forces[e.target].fx -= fx;
    forces[e.target].fy -= fy;
  }

  for (const n of nodes) {
    const p = pos[n.id];
    forces[n.id].fx += (WIDTH / 2 - p.x) * CENTER_STRENGTH * alpha;
    forces[n.id].fy += (HEIGHT / 2 - p.y) * CENTER_STRENGTH * alpha;
  }

  for (const n of nodes) {
    const id = n.id;
    if (draggedId === id) continue;
    const v = vel[id];
    v.vx = (v.vx + forces[id].fx) * DAMPING;
    v.vy = (v.vy + forces[id].fy) * DAMPING;
    pos[id].x += v.vx;
    pos[id].y += v.vy;
  }

  resolveCollisions(nodes, pos, draggedId);
}

// Direct collision resolution, not just a force - a spring can settle at
// equilibrium with residual overlap if it's not strong enough. This
// guarantees no two boxes end up visually stacked, by directly separating
// any pair still closer than COLLISION_DIST. Returns whether anything moved.
function resolveCollisions(nodes, pos, draggedId) {
  let collided = false;
  for (let pass = 0; pass < 2; pass++) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i].id;
        const b = nodes[j].id;
        let dx = pos[b].x - pos[a].x;
        let dy = pos[b].y - pos[a].y;
        let dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 1) {
          dx = Math.random() - 0.5;
          dy = Math.random() - 0.5;
          dist = 1;
        }
        if (dist < COLLISION_DIST) {
          const overlap = COLLISION_DIST - dist;
          const ux = dx / dist;
          const uy = dy / dist;
          const aDraggable = draggedId !== a;
          const bDraggable = draggedId !== b;
          const share = aDraggable && bDraggable ? 0.5 : 1;
          if (aDraggable) {
            pos[a].x -= ux * overlap * share;
            pos[a].y -= uy * overlap * share;
          }
          if (bDraggable) {
            pos[b].x += ux * overlap * share;
            pos[b].y += uy * overlap * share;
          }
          collided = true;
        }
      }
    }
  }
  return collided;
}

export default function GraphViewer({ highlight }) {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [error, setError] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [hoveredEdgeKey, setHoveredEdgeKey] = useState(null);
  const [viewBox, setViewBox] = useState({ x: 0, y: 0, w: WIDTH, h: HEIGHT });
  const [, forceRender] = useState(0);

  const svgRef = useRef(null);
  const posRef = useRef({});
  const velRef = useRef({});
  const alphaRef = useRef(1);
  const dragRef = useRef(null); // { id } while dragging a node
  const panRef = useRef(null); // { startX, startY, startViewBox } while panning

  useEffect(() => {
    getGraph()
      .then((data) => {
        // Pre-solve synchronously before the first paint - otherwise the
        // graph opens on the raw seed circle and visibly untangles itself
        // over the next second or two. Running the same physics here, just
        // without waiting for animation frames, means it opens already
        // spread out and readable.
        const pos = seedPositions(data.nodes);
        const vel = Object.fromEntries(data.nodes.map((n) => [n.id, { vx: 0, vy: 0 }]));
        let alpha = 1;
        for (let i = 0; i < 260; i++) {
          simulationStep(data.nodes, data.edges, pos, vel, alpha, null);
          alpha *= ALPHA_DECAY;
        }

        posRef.current = pos;
        velRef.current = vel;
        alphaRef.current = alpha;
        setNodes(data.nodes);
        setEdges(data.edges);
      })
      .catch((err) => setError(err.message));
  }, []);

  // Clicking a node in the chat's "View on graph" link should show that
  // highlight cleanly, not whatever was locally selected before.
  useEffect(() => {
    if (highlight) setSelectedId(null);
  }, [highlight]);

  // --- physics loop -------------------------------------------------
  useEffect(() => {
    if (nodes.length === 0) return;
    let raf;

    const tick = () => {
      const alpha = alphaRef.current;
      const pos = posRef.current;
      const draggedId = dragRef.current?.id ?? null;

      if (alpha > 0.001) {
        simulationStep(nodes, edges, pos, velRef.current, alpha, draggedId);
        alphaRef.current *= ALPHA_DECAY;
        forceRender((t) => t + 1);
      } else if (resolveCollisions(nodes, pos, draggedId)) {
        // Simulation's at rest, but still guarantee no permanent overlap -
        // e.g. if a drag shoved two nodes back together after settling.
        forceRender((t) => t + 1);
      }
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [nodes, edges]);

  const toSvgPoint = useCallback((clientX, clientY) => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const pt = svg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return { x: 0, y: 0 };
    const transformed = pt.matrixTransform(ctm.inverse());
    return { x: transformed.x, y: transformed.y };
  }, []);

  // --- drag / pan handlers -------------------------------------------
  const handleNodeMouseDown = (id) => (e) => {
    e.stopPropagation();
    dragRef.current = { id };
    alphaRef.current = Math.max(alphaRef.current, 0.6);
  };

  const handleBackgroundMouseDown = (e) => {
    setSelectedId(null);
    panRef.current = {
      startClientX: e.clientX,
      startClientY: e.clientY,
      startViewBox: { ...viewBox },
    };
  };

  useEffect(() => {
    const handleMouseMove = (e) => {
      if (dragRef.current) {
        const p = toSvgPoint(e.clientX, e.clientY);
        posRef.current[dragRef.current.id] = { x: p.x, y: p.y };
        velRef.current[dragRef.current.id] = { vx: 0, vy: 0 };
        forceRender((t) => t + 1);
      } else if (panRef.current) {
        const { startClientX, startClientY, startViewBox } = panRef.current;
        const svg = svgRef.current;
        const scale = startViewBox.w / (svg?.clientWidth || WIDTH);
        setViewBox({
          ...startViewBox,
          x: startViewBox.x - (e.clientX - startClientX) * scale,
          y: startViewBox.y - (e.clientY - startClientY) * scale,
        });
      }
    };
    const handleMouseUp = () => {
      dragRef.current = null;
      panRef.current = null;
    };
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [toSvgPoint]);

  const handleWheel = (e) => {
    e.preventDefault();
    const zoomFactor = e.deltaY > 0 ? 1.1 : 0.9;
    const cursor = toSvgPoint(e.clientX, e.clientY);
    setViewBox((vb) => {
      const newW = Math.min(Math.max(vb.w * zoomFactor, 250), WIDTH * 2.5);
      const newH = newW * (HEIGHT / WIDTH);
      const ratio = newW / vb.w;
      return {
        w: newW,
        h: newH,
        x: cursor.x - (cursor.x - vb.x) * ratio,
        y: cursor.y - (cursor.y - vb.y) * ratio,
      };
    });
  };

  const resetView = () => setViewBox({ x: 0, y: 0, w: WIDTH, h: HEIGHT });

  // --- highlight resolution -------------------------------------------
  const neighborsOf = useMemo(() => {
    const map = {};
    for (const n of nodes) map[n.id] = new Set([n.id]);
    for (const e of edges) {
      map[e.source]?.add(e.target);
      map[e.target]?.add(e.source);
    }
    return map;
  }, [nodes, edges]);

  const activeSet = useMemo(() => {
    if (selectedId) return neighborsOf[selectedId] || new Set([selectedId]);
    if (highlight?.entities?.length) return new Set(highlight.entities);
    return null; // null = nothing dimmed, show everything plainly
  }, [selectedId, highlight, neighborsOf]);

  const selectedNode = nodes.find((n) => n.id === selectedId);
  const selectedRelationships = useMemo(() => {
    if (!selectedId) return [];
    return edges
      .filter((e) => e.source === selectedId || e.target === selectedId)
      .map((e) =>
        e.source === selectedId
          ? { text: `→ ${e.label} → ${e.target}`, via: e.via }
          : { text: `← ${e.label} ← ${e.source}`, via: e.via }
      );
  }, [selectedId, edges]);

  if (error) return <div className="graph-viewer error">{error}</div>;
  if (nodes.length === 0) return <div className="graph-viewer empty">Loading graph…</div>;

  return (
    <div className="graph-viewer">
      <div className="graph-viewer-header">
        <h2>Knowledge Graph</h2>
        <p>
          Drag a table to move it, click one to list its relationships in the panel, hover an edge
          for its join label, scroll to zoom, drag the background to pan. This is the exact
          structure the agent traverses for join-path hints.
        </p>
        <div className="graph-toolbar">
          {(selectedId || highlight?.entities?.length > 0) && (
            <button
              className="graph-clear-highlight"
              onClick={() => {
                setSelectedId(null);
                highlight?.onClear?.();
              }}
            >
              Clear selection
            </button>
          )}
          <button className="graph-clear-highlight" onClick={resetView}>
            Reset view
          </button>
        </div>
      </div>

      <div className="graph-canvas-row">
        <svg
          ref={svgRef}
          viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`}
          className="graph-svg"
          onMouseDown={handleBackgroundMouseDown}
          onWheel={handleWheel}
        >
          <g>
            {edges.map((e, i) => {
              const source = posRef.current[e.source];
              const target = posRef.current[e.target];
              if (!source || !target) return null;
              const key = `${e.source}-${e.target}-${i}`;
              const edgeActive = activeSet ? activeSet.has(e.source) && activeSet.has(e.target) : true;
              const dimmed = activeSet ? !edgeActive : false;
              return (
                <g
                  key={key}
                  onMouseEnter={() => setHoveredEdgeKey(key)}
                  onMouseLeave={() => setHoveredEdgeKey(null)}
                >
                  {/* Fat invisible line under the visible one - a 1.5px
                      stroke is hard to hover precisely, and labels are now
                      hover-only, so accurate hit-testing matters. */}
                  <line
                    x1={source.x}
                    y1={source.y}
                    x2={target.x}
                    y2={target.y}
                    className="graph-edge-hit"
                  />
                  <line
                    x1={source.x}
                    y1={source.y}
                    x2={target.x}
                    y2={target.y}
                    className={`graph-edge${edgeActive && activeSet ? " highlighted" : ""}${dimmed ? " dimmed" : ""}`}
                  />
                </g>
              );
            })}
          </g>

          <g>
            {nodes.map((node) => {
              const p = posRef.current[node.id];
              if (!p) return null;
              const active = activeSet ? activeSet.has(node.id) : true;
              const dimmed = activeSet ? !active : false;
              const isSelected = selectedId === node.id;
              return (
                <g
                  key={node.id}
                  transform={`translate(${p.x - NODE_W / 2}, ${p.y - NODE_H / 2})`}
                  onMouseDown={handleNodeMouseDown(node.id)}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedId((cur) => (cur === node.id ? null : node.id));
                    highlight?.onClear?.();
                  }}
                  className="graph-node-group"
                >
                  <title>{node.description}</title>
                  <rect
                    width={NODE_W}
                    height={NODE_H}
                    rx={8}
                    className={`graph-node ${node.kind}${active && activeSet ? " highlighted" : ""}${dimmed ? " dimmed" : ""}${isSelected ? " selected" : ""}`}
                  />
                  <text x={NODE_W / 2} y={NODE_H / 2 + 4} textAnchor="middle" className="graph-node-label">
                    {node.id}
                  </text>
                </g>
              );
            })}
          </g>

          {/* Top layer, rendered last so a label is never painted over by
              a node passing behind it. Every edge gets one, styled subtle
              by default (small, muted) so 13 of them read as fine detail
              rather than noise - hovering or selecting a node brightens
              just the relevant ones, rather than needing to hunt with the
              mouse to see any label at all. */}
          <g>
            {edges.map((e, i) => {
              const key = `${e.source}-${e.target}-${i}`;
              const source = posRef.current[e.source];
              const target = posRef.current[e.target];
              if (!source || !target) return null;
              const edgeActive = activeSet ? activeSet.has(e.source) && activeSet.has(e.target) : true;
              const dimmed = activeSet ? !edgeActive : false;
              const emphasized = hoveredEdgeKey === key || (activeSet && edgeActive);
              const midX = (source.x + target.x) / 2;
              const midY = (source.y + target.y) / 2;
              return (
                <g key={key} className={`graph-edge-label-group${emphasized ? " emphasized" : ""}${dimmed ? " dimmed" : ""}`}>
                  <rect
                    x={midX - e.label.length * 3.4 - 6}
                    y={midY - 9}
                    width={e.label.length * 6.8 + 12}
                    height={16}
                    rx={4}
                    className="graph-edge-label-bg"
                  />
                  <text x={midX} y={midY + 3} textAnchor="middle" className="graph-edge-label">
                    {e.label}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>

        {selectedNode && (
          <div className="graph-inspector">
            <div className={`graph-inspector-kind ${selectedNode.kind}`}>{selectedNode.kind}</div>
            <h3>{selectedNode.id}</h3>
            <p>{selectedNode.description}</p>
            <div className="graph-inspector-rels-label">Relationships ({selectedRelationships.length})</div>
            <ul className="graph-inspector-rels">
              {selectedRelationships.map((r, i) => (
                <li key={i}>
                  <span>{r.text}</span>
                  <span className="graph-inspector-via">{r.via}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="graph-legend">
        <span className="graph-legend-item">
          <span className="graph-legend-swatch dimension" /> Dimension
        </span>
        <span className="graph-legend-item">
          <span className="graph-legend-swatch fact" /> Fact
        </span>
      </div>
    </div>
  );
}
