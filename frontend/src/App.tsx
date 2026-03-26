import { useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ChatProvider } from "@/contexts/ChatContext";
import Sidebar from "@/components/Sidebar";
import Index from "./pages/Index";
import ChatPage from "./pages/ChatPage";
import ChatDetailPage from "./pages/ChatDetailPage";
import ProjectsPage from "./pages/ProjectsPage";
import ProjectDetailPage from "./pages/ProjectDetailPage";
import KnowledgeGraphPage from "./pages/KnowledgeGraphPage";
import SettingsPage from "./pages/SettingsPage";
import NotFound from "./pages/NotFound";

const AppLayout = () => {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const check = () => setCollapsed(window.innerWidth < 768);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  return (
    <div className="flex h-screen overflow-hidden bg-nexus-bg">
      <Sidebar collapsed={collapsed} />
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Routes>
          <Route path="/" element={<Index />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/chat/:id" element={<ChatDetailPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
          <Route path="/graph" element={<KnowledgeGraphPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
};

const App = () => (
  <ThemeProvider>
    <ChatProvider>
      <BrowserRouter>
        <AppLayout />
      </BrowserRouter>
    </ChatProvider>
  </ThemeProvider>
);

export default App;
