import "antd/dist/reset.css";
import "./src/styles/global.css";

import AuthProvider from "./src/hooks/provider";

export const wrapRootElement = AuthProvider;

// DEMO: 强制站点在进入与刷新时使用 dark 模式，避免首屏闪烁及模式不一致
export const onClientEntry = () => {
  try {
    if (typeof document !== "undefined") {
      const root = document.documentElement;
      // 统一设置 HTML 根节点类
      root.classList.add("dark");
      root.classList.remove("light");
    }
    if (typeof localStorage !== "undefined") {
      // 持久化为 dark，确保刷新后仍为 dark
      localStorage.setItem("darkmode", "dark");
    }
  } catch (e) {
    // no-op in demo
  }
};
