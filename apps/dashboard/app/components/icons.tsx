import type { ReactNode } from "react";

function Icon({ children, className = "h-[1em] w-[1em]" }: { children: ReactNode; className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`inline-block shrink-0 align-[-0.125em] ${className}`}
    >
      {children}
    </svg>
  );
}

type Props = { className?: string };

export const Check = (props: Props) => <Icon {...props}><path d="m4 12.5 5.5 5.5L20 6.5" /></Icon>;
export const Cross = (props: Props) => <Icon {...props}><path d="M5.5 5.5l13 13M18.5 5.5l-13 13" /></Icon>;
export const Warn = (props: Props) => <Icon {...props}><path d="M12 3 2 20.5h20L12 3Z" /><path d="M12 10v4.5M12 17.5v.01" /></Icon>;
export const Pause = (props: Props) => <Icon {...props}><path d="M8 5v14M16 5v14" /></Icon>;
export const Play = (props: Props) => <Icon {...props}><path d="M7 4.5v15l12-7.5L7 4.5Z" /></Icon>;
export const Bolt = (props: Props) => <Icon {...props}><path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H12L13 2Z" /></Icon>;
export const Page = (props: Props) => <Icon {...props}><path d="M6 2.5h8l4.5 4.5v14.5H6v-19Z" /><path d="M9.5 12h5M9.5 16h5" /></Icon>;
export const Clock = (props: Props) => <Icon {...props}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.5 2" /></Icon>;
export const Chevron = (props: Props) => <Icon {...props}><path d="m6 15 6-6 6 6" /></Icon>;
export const Link = (props: Props) => <Icon {...props}><path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6" /></Icon>;
export const Download = (props: Props) => <Icon {...props}><path d="M12 3.5v11M7 10l5 5 5-5M4.5 20.5h15" /></Icon>;
