import { useEffect, useRef } from "react";
import * as d3 from "d3";
import type { GraphData } from "@/lib/memory-api";

interface KnowledgeGraphProps {
  data: GraphData;
  width?: number;
  height?: number;
}

interface SimNode extends d3.SimulationNodeDatum {
  id: string;
  name: string;
  type: string;
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  type: string;
  label: string | null;
}

const ENTITY_COLORS: Record<string, string> = {
  Person: "#6366f1",
  Concept: "#8b5cf6",
  Technology: "#06b6d4",
  Organisation: "#f59e0b",
  Task: "#10b981",
  Other: "#6b7280",
};

const getEntityColor = (type: string): string => ENTITY_COLORS[type] || ENTITY_COLORS.Other;

const KnowledgeGraph = ({ data, width = 800, height = 600 }: KnowledgeGraphProps) => {
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!svgRef.current || !data.nodes.length) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const nodes: SimNode[] = data.nodes.map((n) => ({ ...n }));
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));

    const links: SimLink[] = data.edges
      .filter((e) => nodeMap.has(e.source) && nodeMap.has(e.target))
      .map((e) => ({
        source: e.source,
        target: e.target,
        type: e.type,
        label: e.label,
      }));

    const g = svg.append("g");

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on("zoom", (event) => g.attr("transform", event.transform));

    svg.call(zoom);

    svg.append("defs").append("marker")
      .attr("id", "arrowhead")
      .attr("viewBox", "-0 -5 10 10")
      .attr("refX", 25)
      .attr("refY", 0)
      .attr("orient", "auto")
      .attr("markerWidth", 6)
      .attr("markerHeight", 6)
      .append("path")
      .attr("d", "M0,-5L10,0L0,5")
      .attr("fill", "#6b7280");

    const simulation = d3.forceSimulation(nodes)
      .force("link", d3.forceLink<SimNode, SimLink>(links).id((d) => d.id).distance(150))
      .force("charge", d3.forceManyBody().strength(-400))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide().radius(50));

    const link = g.append("g")
      .attr("class", "links")
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", "#4b5563")
      .attr("stroke-width", 1.5)
      .attr("stroke-opacity", 0.6)
      .attr("marker-end", "url(#arrowhead)");

    const linkLabels = g.append("g")
      .attr("class", "link-labels")
      .selectAll("text")
      .data(links)
      .join("text")
      .attr("font-size", 10)
      .attr("fill", "#9ca3af")
      .attr("text-anchor", "middle")
      .text((d) => d.label || d.type);

    const dragBehavior = d3.drag<SVGGElement, SimNode>()
      .on("start", (event, d) => {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on("drag", (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on("end", (event, d) => {
        if (!event.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });

    const node = g.append("g")
      .attr("class", "nodes")
      .selectAll<SVGGElement, SimNode>("g")
      .data(nodes)
      .join("g")
      .call(dragBehavior);

    node.append("circle")
      .attr("r", 20)
      .attr("fill", (d) => getEntityColor(d.type))
      .attr("stroke", "#1f2937")
      .attr("stroke-width", 2)
      .style("cursor", "grab");

    node.append("text")
      .attr("dy", 35)
      .attr("text-anchor", "middle")
      .attr("font-size", 12)
      .attr("fill", "#e5e7eb")
      .attr("font-weight", 500)
      .text((d) => d.name);

    node.append("title")
      .text((d) => `${d.name} (${d.type})`);

    simulation.on("tick", () => {
      link
        .attr("x1", (d) => (d.source as SimNode).x!)
        .attr("y1", (d) => (d.source as SimNode).y!)
        .attr("x2", (d) => (d.target as SimNode).x!)
        .attr("y2", (d) => (d.target as SimNode).y!);

      linkLabels
        .attr("x", (d) => ((d.source as SimNode).x! + (d.target as SimNode).x!) / 2)
        .attr("y", (d) => ((d.source as SimNode).y! + (d.target as SimNode).y!) / 2);

      node.attr("transform", (d) => `translate(${d.x},${d.y})`);
    });

    return () => {
      simulation.stop();
    };
  }, [data, width, height]);

  if (!data.nodes.length) {
    return (
      <div
        className="flex items-center justify-center text-nexus-muted text-sm"
        style={{ width, height }}
      >
        No entities in graph yet. Start chatting to build your knowledge graph.
      </div>
    );
  }

  return (
    <svg
      ref={svgRef}
      width={width}
      height={height}
      className="bg-nexus-bg rounded-lg border border-nexus-border"
    />
  );
};

export default KnowledgeGraph;
