import React, { useState } from "react";
import { getLocalStorage, setLocalStorage } from "../components/utils";
import { message } from "antd";

export interface IUser {
  name: string;
  email?: string;
  username?: string;
  employee_id?: string;
  avatar_url?: string;
  metadata?: any;
  id?: string;
  tenant_id?: string;
}

export interface AppContextType {
  user: IUser | null;
  setUser: any;
  logout: any;
  cookie_name: string;
  darkMode: string;
  setDarkMode: any;
  isLoggedIn: boolean;
  token: string | null;
  setAuth: (user: IUser, token: string) => void;
}

const cookie_name = "coral_app_cookie_";

export const appContext = React.createContext<AppContextType>(
  {} as AppContextType
);
const Provider = ({ children }: any) => {
  const storedValue = getLocalStorage("darkmode", false);
  const [darkMode, setDarkMode] = useState(
    storedValue === null ? "dark" : storedValue === "dark" ? "dark" : "light"
  );

  // Initialize auth state from localStorage (only on client side)
  const [userState, setUserState] = useState<IUser | null>(() => {
    if (typeof window === "undefined") return null;
    const storedUser = localStorage.getItem("magentic_ui_user");
    if (storedUser) {
      try {
        return JSON.parse(storedUser);
      } catch (e) {
        console.error("Failed to parse stored user:", e);
        return null;
      }
    }
    return null;
  });

  const [token, setToken] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return localStorage.getItem("magentic_ui_token");
  });

  const isLoggedIn = !!(token && userState);

  const updateDarkMode = (darkMode: string) => {
    setDarkMode(darkMode);
    setLocalStorage("darkmode", darkMode, false);
    // DEMO: 同步 html 根元素类，避免组件未挂载前类名不同步
    if (typeof document !== "undefined") {
      const root = document.documentElement;
      if (darkMode === "dark") {
        root.classList.add("dark");
        root.classList.remove("light");
      } else {
        root.classList.remove("dark");
        root.classList.add("light");
      }
    }
  };

  // DEMO: 首次挂载时强制使用 dark，并同步 html 类与本地存储
  React.useEffect(() => {
    try {
      if (typeof document !== "undefined") {
        const root = document.documentElement;
        root.classList.add("dark");
        root.classList.remove("light");
      }
      setLocalStorage("darkmode", "dark", false);
      setDarkMode("dark");
    } catch (e) {
      // no-op in demo
    }
  }, []);

  const setUser = (user: IUser | null) => {
    if (user?.email) {
      setLocalStorage("user_email", user.email, false);
    }
    setUserState(user);
  };

  const setAuth = (user: IUser, authToken: string) => {
    setUserState(user);
    setToken(authToken);
    localStorage.setItem("magentic_ui_user", JSON.stringify(user));
    localStorage.setItem("magentic_ui_token", authToken);
    if (user.email) {
      setLocalStorage("user_email", user.email, false);
    }
  };

  const logout = () => {
    // Clear auth state
    setUserState(null);
    setToken(null);
    localStorage.removeItem("magentic_ui_user");
    localStorage.removeItem("magentic_ui_token");
    localStorage.removeItem("magentic_ui_refresh_token");
    localStorage.removeItem("weknora_user");
    localStorage.removeItem("weknora_token");
    localStorage.removeItem("weknora_refresh_token");
    message.success("已登出");
  };

  return (
    <appContext.Provider
      value={{
        user: userState,
        setUser,
        logout,
        cookie_name,
        darkMode,
        setDarkMode: updateDarkMode,
        isLoggedIn,
        token,
        setAuth,
      }}
    >
      {children}
    </appContext.Provider>
  );
};

export default ({ element }: any) => <Provider>{element}</Provider>;
