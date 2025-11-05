import * as React from "react";
import { appContext } from "../hooks/provider";
import { useConfigStore } from "../hooks/store";
import "antd/dist/reset.css";
import { ConfigProvider, theme } from "antd";
import { SessionManager } from "./views/manager";
import { useTranslation } from 'react-i18next'
import { navigate } from "gatsby";

const classNames = (...classes: (string | undefined | boolean)[]) => {
  return classes.filter(Boolean).join(" ");
};

type Props = {
  title: string;
  link: string;
  children?: React.ReactNode;
  showHeader?: boolean;
  restricted?: boolean;
  meta?: any;
  activeTab?: string;
  onTabChange?: (tab: string) => void;
};

const MagenticUILayout = ({
  meta,
  title,
  link,
  showHeader = true,
  restricted = false,
  activeTab,
  onTabChange,
}: Props) => {
  const { t, i18n } = useTranslation();
  const { darkMode, user, setUser, isLoggedIn } = React.useContext(appContext);
  const { sidebar } = useConfigStore();
  const { isExpanded } = sidebar;
  const [isMobileMenuOpen, setIsMobileMenuOpen] = React.useState(false);

  // Check authentication on mount and when auth state changes
  React.useEffect(() => {
    i18n.changeLanguage('zh');

    // Only check and redirect on client side
    if (typeof window === "undefined") return;

    const currentPath = window.location.pathname;
    const isLoginPage = currentPath === '/login' || currentPath.startsWith('/login');
    
    // Check authentication state from localStorage first (most reliable)
    const storedUser = localStorage.getItem("magentic_ui_user");
    const storedToken = localStorage.getItem("magentic_ui_token");
    
    // Parse and validate user data
    let hasValidAuth = false;
    if (storedUser && storedToken) {
      try {
        const userObj = JSON.parse(storedUser);
        // Check if it's a valid user object (not empty, has id or employee_id)
        if (userObj && (userObj.id || userObj.employee_id || userObj.username)) {
          hasValidAuth = true;
        }
      } catch (e) {
        // Invalid JSON - clear corrupted data
        console.error('Invalid user data in localStorage:', e);
        localStorage.removeItem("magentic_ui_user");
        localStorage.removeItem("magentic_ui_token");
      }
    }
    
    // If we're on login page
    if (isLoginPage) {
      // If already authenticated, redirect to home
      if (hasValidAuth && isLoggedIn) {
        navigate('/');
      }
      return;
    }
    
    // Not on login page - must be authenticated
    if (!hasValidAuth || !isLoggedIn) {
      console.log('Not authenticated, redirecting to login...', { 
        hasValidAuth, 
        storedToken: !!storedToken, 
        isLoggedIn,
        currentPath
      });
      navigate('/login');
    }
  }, [isLoggedIn, link]);

  // Close mobile menu on route change
  React.useEffect(() => {
    setIsMobileMenuOpen(false);
  }, [link]);

  React.useEffect(() => {
    document.getElementsByTagName("html")[0].className = `${
      darkMode === "dark" ? "dark bg-primary" : "light bg-primary"
    }`;
  }, [darkMode]);

  const layoutContent = (
    <div className="h-screen flex">
      {/* Content area */}
      <div
        className={classNames(
          "flex-1 flex flex-col min-h-screen",
          "transition-all duration-300 ease-in-out"
        )}
      >
        <ConfigProvider
          theme={{
            token: {
              borderRadius: 4,
              colorBgBase: darkMode === "dark" ? "#2a2a2a" : "#ffffff",
            },
            algorithm:
              darkMode === "dark"
                ? theme.darkAlgorithm
                : theme.defaultAlgorithm,
          }}
        >
          <main className="flex-1 text-primary" style={{ height: "100%" }}>
            <SessionManager />
          </main>
        </ConfigProvider>
      </div>
    </div>
  );

  if (restricted) {
    return (
      <appContext.Consumer>
        {(context: any) => {
          if (context.user) {
            return layoutContent;
          }
          return null;
        }}
      </appContext.Consumer>
    );
  }

  return layoutContent;
};

export default MagenticUILayout;
