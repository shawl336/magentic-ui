import { RcFile } from "antd/es/upload";
import { IStatus } from "./types/app";

export const getServerUrl = () => {
  // For server-side rendering, use relative path
  if (typeof window === "undefined") {
    // If GATSBY_API_URL is set and is a relative path, use it
    if (process.env.GATSBY_API_URL && process.env.GATSBY_API_URL.startsWith("/")) {
      return process.env.GATSBY_API_URL;
    }
    return "/api";
  }
  
  // Get current hostname and protocol at runtime
  const hostname = window.location.hostname;
  const protocol = window.location.protocol;
  const isLocalhost = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "0.0.0.0";
  
  // Check if GATSBY_API_URL is set and is a relative path
  if (process.env.GATSBY_API_URL && process.env.GATSBY_API_URL.startsWith("/")) {
    // Use relative path - Gatsby proxy will handle it
    return process.env.GATSBY_API_URL;
  }
  
  // If GATSBY_API_URL is set but contains localhost, ignore it for remote access
  // and use dynamic hostname instead
  if (process.env.GATSBY_API_URL && process.env.GATSBY_API_URL.includes("localhost") && !isLocalhost) {
    // For remote access, use the actual hostname instead of localhost
    const backendPort = "8081";
    return `${protocol}//${hostname}:${backendPort}/api`;
  }
  
  // If GATSBY_API_URL is set and doesn't contain localhost, use it
  if (process.env.GATSBY_API_URL && !process.env.GATSBY_API_URL.includes("localhost")) {
    return process.env.GATSBY_API_URL;
  }
  
  // For remote access (non-localhost), connect directly to backend on port 8081
  if (!isLocalhost) {
    // This assumes backend is accessible on the same IP as frontend
    const backendPort = "8081";
    return `${protocol}//${hostname}:${backendPort}/api`;
  }
  
  // For localhost access, use proxy through Gatsby dev server
  const port = window.location.port;
  const baseUrl = `${protocol}//${hostname}${port ? `:${port}` : ""}`;
  return `${baseUrl}/api`;
};

/**
 * Get the base URL for file access (files, images, etc.)
 * This always returns the direct backend URL, not through Gatsby proxy
 * because file requests need to go directly to the backend server
 */
export const getFileServerUrl = () => {
  // For server-side rendering, return empty string (relative path)
  if (typeof window === "undefined") {
    return "";
  }
  
  // Get current hostname and protocol at runtime
  const hostname = window.location.hostname;
  const protocol = window.location.protocol;
  const backendPort = "8081";
  
  // Always use direct backend connection for files
  // This ensures files are accessible regardless of how the frontend is accessed
  return `${protocol}//${hostname}:${backendPort}`;
};

export function setCookie(name: string, value: any, days: number) {
  let expires = "";
  if (days) {
    const date = new Date();
    date.setTime(date.getTime() + days * 24 * 60 * 60 * 1000);
    expires = "; expires=" + date.toUTCString();
  }
  document.cookie = name + "=" + (value || "") + expires + "; path=/";
}

export function getCookie(name: string) {
  const nameEQ = name + "=";
  const ca = document.cookie.split(";");
  for (let i = 0; i < ca.length; i++) {
    let c = ca[i];
    while (c.charAt(0) == " ") c = c.substring(1, c.length);
    if (c.indexOf(nameEQ) == 0) return c.substring(nameEQ.length, c.length);
  }
  return null;
}
export function setLocalStorage(
  name: string,
  value: any,
  stringify: boolean = true
) {
  if (stringify) {
    localStorage.setItem(name, JSON.stringify(value));
  } else {
    localStorage.setItem(name, value);
  }
}

export function getLocalStorage(name: string, stringify: boolean = true): any {
  if (typeof window !== "undefined") {
    const value = localStorage.getItem(name);
    try {
      if (stringify) {
        return JSON.parse(value!);
      } else {
        return value;
      }
    } catch (e) {
      return null;
    }
  } else {
    return null;
  }
}

export function fetchJSON(
  url: string | URL,
  payload: any = {},
  onSuccess: (data: any) => void,
  onError: (error: IStatus) => void,
  onFinal: () => void = () => {}
) {
  return fetch(url, payload)
    .then(function (response) {
      if (response.status !== 200) {
        console.log(
          "Looks like there was a problem. Status Code: " + response.status,
          response
        );
        response.json().then(function (data) {
          console.log("Error data", data);
        });
        onError({
          status: false,
          message:
            "Connection error " + response.status + " " + response.statusText,
        });
        return;
      }
      return response.json().then(function (data) {
        onSuccess(data);
      });
    })
    .catch(function (err) {
      console.log("Fetch Error :-S", err);
      onError({
        status: false,
        message: `There was an error connecting to server. (${err}) `,
      });
    })
    .finally(() => {
      onFinal();
    });
}

export function eraseCookie(name: string) {
  document.cookie = name + "=; Path=/; Expires=Thu, 01 Jan 1970 00:00:01 GMT;";
}

export function truncateText(text: string, length = 50) {
  if (text.length > length) {
    return text.substring(0, length) + " ...";
  }
  return text;
}

export const fetchVersion = () => {
  const versionUrl = getServerUrl() + "/version";
  return fetch(versionUrl)
    .then((response) => response.json())
    .then((data) => {
      return data;
    })
    .catch((error) => {
      console.error("Error:", error);
      return null;
    });
};

export const convertFilesToBase64 = async (files: RcFile[] = []) => {
  return Promise.all(
    files.map(async (file) => {
      return new Promise<{ name: string; content: string; type: string }>(
        (resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => {
            // Extract base64 content from reader result
            const base64Content = reader.result as string;
            // Remove the data URL prefix (e.g., "data:image/png;base64,")
            const base64Data = base64Content.split(",")[1] || base64Content;
            resolve({ name: file.name, content: base64Data, type: file.type });
          };
          reader.onerror = reject;
          reader.readAsDataURL(file);
        }
      );
    })
  );
};
