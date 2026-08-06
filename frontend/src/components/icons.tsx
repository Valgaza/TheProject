import React from "react";

interface IconProps extends React.SVGProps<SVGSVGElement> {
  name: string;
  size?: number | string;
}

export const Icon: React.FC<IconProps> = ({ name, size = 16, className = "", ...rest }) => {
  const paths: Record<string, React.ReactNode> = {
    chat: <><path d="M3 7c0-1.1.9-2 2-2h11c1.1 0 2 .9 2 2v6c0 1.1-.9 2-2 2H8l-4 3v-3H5a2 2 0 0 1-2-2V7Z"/></>,
    bolt: <><path d="M12 3 5 13h5l-1 8 7-10h-5l1-8Z"/></>,
    folder: <><path d="M3 6c0-1.1.9-2 2-2h3.5l2 2H19c1.1 0 2 .9 2 2v9c0 1.1-.9 2-2 2H5a2 2 0 0 1-2-2V6Z"/></>,
    graph: <><circle cx="6" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="12" cy="14" r="2"/><circle cx="6" cy="19" r="1.6"/><circle cx="18" cy="19" r="1.6"/><path d="M7.5 7l3 6M16.5 7l-3 6M11 15l-4 3M13 15l4 3"/></>,
    cog: <><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/></>,
    plus: <><path d="M12 5v14M5 12h14"/></>,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6"/></>,
    arrowU: <><path d="M12 19V5M6 11l6-6 6 6"/></>,
    paper: <><path d="M21.4 11.6 13 20a5.7 5.7 0 1 1-8-8L13.4 3.6a4 4 0 0 1 5.7 5.7L11 17.6a2.3 2.3 0 0 1-3.3-3.3l7-7"/></>,
    mic: <><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></>,
    search: <><circle cx="11" cy="11" r="6"/><path d="M20 20l-3.5-3.5"/></>,
    inbox: <><path d="M3 12h5l1.5 3h5l1.5-3h5M3 12l3-7h12l3 7M3 12v6a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-6"/></>,
    bell: <><path d="M6 9a6 6 0 1 1 12 0v4l1.5 3h-15L6 13V9ZM10 19a2 2 0 0 0 4 0"/></>,
    book: <><path d="M4 5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 1-2-2V5ZM4 18a2 2 0 0 1 2-2h13"/></>,
    flask: <><path d="M9 3h6M10 3v6L4 19a2 2 0 0 0 1.7 3h12.6A2 2 0 0 0 20 19l-6-10V3"/></>,
    scale: <><path d="M12 3v18M5 7h14M5 13l3-6 3 6a3 3 0 0 1-6 0ZM13 13l3-6 3 6a3 3 0 0 1-6 0Z"/></>,
    code: <><path d="M9 7l-5 5 5 5M15 7l5 5-5 5"/></>,
    coin: <><circle cx="12" cy="12" r="9"/><path d="M9.5 9h4.5a2 2 0 0 1 0 4h-4a2 2 0 0 0 0 4h5M12 6.5V8M12 16v1.5"/></>,
    grid: <><rect x="4" y="4" width="7" height="7" rx="1"/><rect x="13" y="4" width="7" height="7" rx="1"/><rect x="4" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/></>,
    truck: <><path d="M3 7h11v9H3zM14 11h4l3 3v2h-7zM7 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM18 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"/></>,
    spark: <><path d="M12 3v6M12 15v6M3 12h6M15 12h6M5.6 5.6l4.2 4.2M14.2 14.2l4.2 4.2M5.6 18.4l4.2-4.2M14.2 9.8l4.2-4.2"/></>,
    check: <><path d="M5 12l5 5L20 7"/></>,
    chev: <><path d="M9 6l6 6-6 6"/></>,
    chevD: <><path d="M6 9l6 6 6-6"/></>,
    pin: <><path d="M12 2v9M12 14a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z"/></>,
    quote: <><path d="M7 6h4v6H7zM13 6h4v6h-4zM7 12c0 4-2 6-4 6M13 12c0 4-2 6-4 6"/></>,
    speak: <><path d="M11 5 6 9H3v6h3l5 4V5ZM16 8a5 5 0 0 1 0 8M19 5a9 9 0 0 1 0 14"/></>,
    moon: <><path d="M20 14A8 8 0 1 1 10 4a7 7 0 0 0 10 10Z"/></>,
    user: <><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></>,
    lock: <><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></>,
    db:   <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></>,
    cloud: <><path d="M7 18a5 5 0 0 1-1-9.9 7 7 0 0 1 13.5 2A4.5 4.5 0 0 1 18 18H7Z"/></>,
    shield:<><path d="M12 3 4 6v6c0 4.5 3.4 8.5 8 9 4.6-.5 8-4.5 8-9V6l-8-3Z"/></>,
    close: <><path d="M6 6l12 12M18 6 6 18"/></>,
  };
  const path = paths[name] || paths.chat;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="1.5"
         strokeLinecap="round" strokeLinejoin="round"
         className={className} {...rest}>
      {path}
    </svg>
  );
};
