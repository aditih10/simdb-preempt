/** Purely presentational — the "NN / LABEL ─────" editorial section
 * marker reused across the page. No state, no logic. */
interface Props {
  index: string
  label: string
  title?: string
}

export default function SectionHeading({ index, label, title }: Props) {
  return (
    <div className="section-heading">
      <span className="section-index mono">{index}</span>
      <span className="section-label mono">{label}</span>
      <span className="section-rule" />
      {title && <h2 className="section-title">{title}</h2>}
    </div>
  )
}
