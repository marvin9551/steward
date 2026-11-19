import * as React from 'react'

import { cn } from '@/lib/utils'

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {}

const Input = React.forwardRef<HTMLInputElement, InputProps>(({ className, type, ...props }, ref) => (
  <input
    type={type}
    className={cn(
      'flex h-11 w-full rounded-md border border-border bg-background px-3 py-2 text-[18px] text-foreground transition-colors placeholder:text-muted-foreground focus-visible:border-info focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/20 disabled:cursor-not-allowed disabled:opacity-60 disabled:bg-secondary',
      className,
    )}
    ref={ref}
    {...props}
  />
))
Input.displayName = 'Input'

export { Input }
