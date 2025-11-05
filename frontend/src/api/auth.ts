// Auth API module similar to weknora-frontend
// Use the same backend as weknora-frontend
const BASE_URL = "http://localhost:8080";

// Request helper with authentication
const request = async (
  url: string,
  options: RequestInit = {}
): Promise<any> => {
  const token = localStorage.getItem("magentic_ui_token");
  
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  // Merge any existing headers from options
  if (options.headers) {
    if (options.headers instanceof Headers) {
      options.headers.forEach((value, key) => {
        headers[key] = value;
      });
    } else if (Array.isArray(options.headers)) {
      options.headers.forEach(([key, value]) => {
        headers[key] = value;
      });
    } else {
      Object.assign(headers, options.headers);
    }
  }

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  try {
    const response = await fetch(`${BASE_URL}${url}`, {
      ...options,
      headers,
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({ message: "Request failed" }));
      throw new Error(data.message || `HTTP error! status: ${response.status}`);
    }

    const responseData = await response.json();
    return responseData;
  } catch (error: any) {
    console.error("API request error:", error);
    throw error;
  }
};

// Types
export interface LoginRequest {
  employee_id: string;
  password: string;
}

export interface RegisterRequest {
  username: string;
  employee_id: string;
  password: string;
}

export interface UserInfo {
  id: string;
  username: string;
  employee_id: string;
  avatar?: string;
  tenant_id: string;
  created_at: string;
  updated_at: string;
}

export interface TenantInfo {
  id: string;
  name: string;
  description?: string;
  api_key: string;
  status?: string;
  business?: string;
  owner_id: string;
  storage_quota?: number;
  storage_used?: number;
  created_at: string;
  updated_at: string;
}

export interface LoginResponse {
  success: boolean;
  message?: string;
  user?: {
    id: string;
    username: string;
    employee_id: string;
    avatar?: string;
    tenant_id: number;
    is_active: boolean;
    created_at: string;
    updated_at: string;
  };
  tenant?: {
    id: number;
    name: string;
    description: string;
    api_key: string;
    status: string;
    business: string;
    storage_quota: number;
    storage_used: number;
    created_at: string;
    updated_at: string;
  };
  token?: string;
  refresh_token?: string;
}

export interface RegisterResponse {
  success: boolean;
  message?: string;
  data?: {
    user: {
      id: string;
      username: string;
      email: string;
    };
    tenant: {
      id: string;
      name: string;
      api_key: string;
    };
  };
}

// Auth API functions
export async function login(data: LoginRequest): Promise<LoginResponse> {
  try {
    const response = await request("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify(data),
    });
    return response as LoginResponse;
  } catch (error: any) {
    return {
      success: false,
      message: error.message || "登录失败",
    };
  }
}

export async function register(data: RegisterRequest): Promise<RegisterResponse> {
  try {
    const response = await request("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify(data),
    });
    return response as RegisterResponse;
  } catch (error: any) {
    console.error("Register error:", error);
    return {
      success: false,
      message: error.message || "注册失败",
    };
  }
}

export async function getCurrentUser(): Promise<{
  success: boolean;
  data?: { user: UserInfo; tenant: TenantInfo };
  message?: string;
}> {
  try {
    const response = await request("/api/v1/auth/me");
    return response;
  } catch (error: any) {
    return {
      success: false,
      message: error.message || "获取用户信息失败",
    };
  }
}

export async function getCurrentTenant(): Promise<{
  success: boolean;
  data?: TenantInfo;
  message?: string;
}> {
  try {
    const response = await request("/api/v1/auth/tenant");
    return response;
  } catch (error: any) {
    return {
      success: false,
      message: error.message || "获取租户信息失败",
    };
  }
}

export async function refreshToken(
  refreshToken: string
): Promise<{
  success: boolean;
  data?: { token: string; refreshToken: string };
  message?: string;
}> {
  try {
    const response: any = await request("/api/v1/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refreshToken }),
    });

    if (response && response.success) {
      if (response.access_token || response.refresh_token) {
        return {
          success: true,
          data: {
            token: response.access_token,
            refreshToken: response.refresh_token,
          },
        };
      }
    }

    return {
      success: false,
      message: response?.message || "刷新Token失败",
    };
  } catch (error: any) {
    return {
      success: false,
      message: error.message || "刷新Token失败",
    };
  }
}

export async function logout(): Promise<{ success: boolean; message?: string }> {
  try {
    await request("/api/v1/auth/logout", {
      method: "POST",
      body: JSON.stringify({}),
    });
    return {
      success: true,
    };
  } catch (error: any) {
    return {
      success: false,
      message: error.message || "登出失败",
    };
  }
}

export async function validateToken(): Promise<{
  success: boolean;
  valid?: boolean;
  message?: string;
}> {
  try {
    const response = await request("/api/v1/auth/validate");
    return response as { success: boolean; valid?: boolean; message?: string };
  } catch (error: any) {
    return {
      success: false,
      valid: false,
      message: error.message || "Token验证失败",
    };
  }
}

