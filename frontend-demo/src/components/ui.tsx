import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react'

type Variant = 'default' | 'muted' | 'accent' | 'success' | 'danger'

export function Card({
  className = '',
  children,
  ...rest
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`ui-card ${className}`.trim()} {...rest}>
      {children}
    </div>
  )
}

export function CardHeader({
  title,
  subtitle,
  action,
}: {
  title: string
  subtitle?: string
  action?: ReactNode
}) {
  return (
    <div className="ui-card-header">
      <div>
        <h3 className="ui-card-title">{title}</h3>
        {subtitle ? <p className="ui-card-subtitle">{subtitle}</p> : null}
      </div>
      {action ? <div className="ui-card-action">{action}</div> : null}
    </div>
  )
}

export function CardContent({
  className = '',
  children,
  ...rest
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`ui-card-content ${className}`.trim()} {...rest}>
      {children}
    </div>
  )
}

export function Badge({
  variant = 'default',
  children,
  className = '',
}: {
  variant?: Variant
  children: ReactNode
  className?: string
}) {
  return (
    <span className={`ui-badge ui-badge-${variant} ${className}`.trim()}>
      {children}
    </span>
  )
}

export function Separator({ className = '' }: { className?: string }) {
  return <hr className={`ui-separator ${className}`.trim()} />
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'ghost' | 'outline'
  size?: 'sm' | 'md'
}

export function Button({
  variant = 'primary',
  size = 'md',
  className = '',
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={`ui-button ui-button-${variant} ui-button-${size} ${className}`.trim()}
      {...rest}
    >
      {children}
    </button>
  )
}

export function GlowDot({ color = 'accent' }: { color?: 'accent' | 'success' | 'danger' }) {
  return <span className={`ui-glow-dot ui-glow-dot-${color}`} aria-hidden />
}
