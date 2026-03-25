import React, { createContext, useContext, useState } from "react";
import { CHATS, type Chat } from "@/lib/data";

interface ChatContextType {
  activeChatId: string | null;
  setActiveChatId: (id: string | null) => void;
  selectedDomain: string;
  setSelectedDomain: (id: string) => void;
  chats: Chat[];
  traceOpen: boolean;
  setTraceOpen: (open: boolean) => void;
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (v: boolean) => void;
}

const ChatContext = createContext<ChatContextType>({
  activeChatId: null,
  setActiveChatId: () => {},
  selectedDomain: "general",
  setSelectedDomain: () => {},
  chats: CHATS,
  traceOpen: false,
  setTraceOpen: () => {},
  sidebarCollapsed: false,
  setSidebarCollapsed: () => {},
});

export const ChatProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [selectedDomain, setSelectedDomain] = useState("general");
  const [traceOpen, setTraceOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  return (
    <ChatContext.Provider
      value={{ activeChatId, setActiveChatId, selectedDomain, setSelectedDomain, chats: CHATS, traceOpen, setTraceOpen, sidebarCollapsed, setSidebarCollapsed }}
    >
      {children}
    </ChatContext.Provider>
  );
};

export const useChat = () => useContext(ChatContext);
