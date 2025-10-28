import React, { useState } from "react";
import { getLocalStorage, setLocalStorage } from "../components/utils";
import { message } from "antd";

export interface IUser {
  name: string;
  email?: string;
  username?: string;
  avatar_url?: string;
  metadata?: any;
}

export interface AppContextType {
  user: IUser | null;
  setUser: any;
  logout: any;
  cookie_name: string;
  darkMode: string;
  setDarkMode: any;
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

  const logout = () => {
    // setUser(null);
    // eraseCookie(cookie_name);
    console.log("Please implement your own logout logic");
    message.info("Please implement your own logout logic");
  };

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

  // Modify logic here to add your own authentication
  const initUser = {
    name: "Guest User",
    email: getLocalStorage("user_email") || "guestuser@gmail.com",
    username: "guestuser",
  };

  const setUser = (user: IUser | null) => {
    if (user?.email) {
      setLocalStorage("user_email", user.email, false);
    }
    setUserState(user);
  };

  const [userState, setUserState] = useState<IUser | null>(initUser);

  React.useEffect(() => {
    const storedEmail = getLocalStorage("user_email");
    if (storedEmail) {
      setUserState((prevUser) => ({
        ...prevUser,
        email: storedEmail,
        name: storedEmail,
      }));
    }
  }, []);

  return (
    <appContext.Provider
      value={{
        user: userState,
        setUser,
        logout,
        cookie_name,
        darkMode,
        setDarkMode: updateDarkMode,
      }}
    >
      {children}
    </appContext.Provider>
  );
};

export default ({ element }: any) => <Provider>{element}</Provider>;
