import * as React from 'react'
import { cva } from 'class-variance-authority'
import type { VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold leading-none transition-colors',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-secondary text-secondary-foreground',
        outline: 'border-border bg-white/60 text-muted-foreground',
        real: 'border-sky-600/25 bg-sky-50 text-sky-700',
        synthetic: 'border-violet-600/25 bg-violet-50 text-violet-700',
        anomaly: 'border-amber-600/30 bg-amber-50 text-amber-700',
        danger: 'border-primary/30 bg-accent text-accent-foreground',
        // Tier colours. The tier itself is assigned by backend/rank/tiers.py —
        // this only paints what arrived.
        CONFIRMED: 'border-tier-confirmed/30 bg-tier-confirmed/10 text-tier-confirmed',
        CORRELATED: 'border-tier-correlated/30 bg-tier-correlated/10 text-tier-correlated',
        MISSING_EVIDENCE: 'border-tier-missing/30 bg-tier-missing/10 text-tier-missing',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { badgeVariants }
