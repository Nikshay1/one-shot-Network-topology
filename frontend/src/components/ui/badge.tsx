import * as React from 'react'
import { cva } from 'class-variance-authority'
import type { VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium leading-none transition-colors',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-secondary text-secondary-foreground',
        outline: 'border-border text-muted-foreground',
        real: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
        synthetic: 'border-violet-500/30 bg-violet-500/10 text-violet-300',
        anomaly: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
        danger: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
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
