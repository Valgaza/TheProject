interface PipelineBadgeProps {
  steps: string[];
}

const PipelineBadge = ({ steps }: PipelineBadgeProps) => {
  return (
    <div className="flex items-center gap-1 mt-2 flex-wrap">
      <span className="text-2xs text-nexus-muted mr-1">Routed via:</span>
      {steps.map((step, i) => (
        <span key={i} className="flex items-center gap-0.5">
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-2xs border border-nexus-border text-nexus-text-secondary bg-nexus-surface">
            {step} <span className="text-nexus-green">✓</span>
          </span>
          {i < steps.length - 1 && <span className="text-nexus-muted text-2xs">→</span>}
        </span>
      ))}
    </div>
  );
};

export default PipelineBadge;
