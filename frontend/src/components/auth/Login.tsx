import * as React from "react";
import { useState, useEffect } from "react";
import { navigate } from "gatsby";
import { message } from "antd";
import { appContext } from "../../hooks/provider";
import { login, register } from "../../api/auth";
import { Button } from "../common/Button";
import "./Auth.css";

const Login = () => {
  const { isLoggedIn, setAuth } = React.useContext(appContext);

  // Redirect if already logged in
  useEffect(() => {
    if (isLoggedIn) {
      navigate("/");
    }
  }, [isLoggedIn]);

  const [loading, setLoading] = useState(false);
  const [isRegisterMode, setIsRegisterMode] = useState(false);

  // Login form data
  const [loginData, setLoginData] = useState({
    employee_id: "",
    password: "",
  });

  // Register form data
  const [registerData, setRegisterData] = useState({
    username: "",
    employee_id: "",
    password: "",
    confirmPassword: "",
  });

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    
    // Validation
    if (!loginData.employee_id || !loginData.password) {
      message.error("请填写完整信息");
      return;
    }

    // Test mode: Skip API validation when username and password are both "admin" or both "guest"
    const isTestMode = 
      (loginData.employee_id.toLowerCase() === "admin" && loginData.password.toLowerCase() === "admin") ||
      (loginData.employee_id.toLowerCase() === "guest" && loginData.password.toLowerCase() === "guest");

    if (isTestMode) {
      // Direct login for testing
      const testUsername = loginData.employee_id.toLowerCase();
      setAuth(
        {
          name: testUsername,
          username: testUsername,
          email: testUsername,
          employee_id: testUsername,
          id: `test_${testUsername}`,
          tenant_id: "test_tenant",
        },
        `test_token_${testUsername}_${Date.now()}`
      );
      message.success("测试模式登录成功！");
      navigate("/");
      return;
    }

    // Normal validation for employee_id
    if (loginData.employee_id.length !== 8 || !/^\d{8}$/.test(loginData.employee_id)) {
      message.error("工号必须是8位数字");
      return;
    }

    setLoading(true);
    try {
      const response = await login({
        employee_id: loginData.employee_id,
        password: loginData.password,
      });

      if (response.success && response.user && response.token) {
        // Save user info and token
        setAuth(
          {
            name: response.user.username,
            username: response.user.username,
            email: response.user.employee_id,
            employee_id: response.user.employee_id,
            id: response.user.id,
            tenant_id: String(response.tenant?.id || ""),
          },
          response.token
        );

        if (response.refresh_token) {
          localStorage.setItem("magentic_ui_refresh_token", response.refresh_token);
        }

        message.success("登录成功！");
        navigate("/");
      } else {
        message.error(response.message || "登录失败，请检查工号或密码");
      }
    } catch (error: any) {
      console.error("Login error:", error);
      message.error(error.message || "登录失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();

    // Validation
    if (!registerData.username || !registerData.employee_id || !registerData.password) {
      message.error("请填写完整信息");
      return;
    }

    if (registerData.employee_id.length !== 8 || !/^\d{8}$/.test(registerData.employee_id)) {
      message.error("工号必须是8位数字");
      return;
    }

    if (registerData.password !== registerData.confirmPassword) {
      message.error("两次输入的密码不一致");
      return;
    }

    if (registerData.password.length < 8 || registerData.password.length > 32) {
      message.error("密码长度必须在8-32位之间");
      return;
    }

    if (!/[a-zA-Z]/.test(registerData.password) || !/\d/.test(registerData.password)) {
      message.error("密码必须包含字母和数字");
      return;
    }

    setLoading(true);
    try {
      const response = await register({
        username: registerData.username,
        employee_id: registerData.employee_id,
        password: registerData.password,
      });

      if (response.success) {
        message.success("注册成功！请登录使用");
        setIsRegisterMode(false);
        setLoginData({
          employee_id: registerData.employee_id,
          password: "",
        });
        setRegisterData({
          username: "",
          employee_id: "",
          password: "",
          confirmPassword: "",
        });
      } else {
        message.error(response.message || "注册失败");
      }
    } catch (error: any) {
      console.error("Register error:", error);
      message.error(error.message || "注册失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-container">
      {!isRegisterMode ? (
        <div className="auth-card">
          <div className="auth-header">
            <h1 className="auth-title">电气装备AI辅助设计平台</h1>
            <p className="auth-subtitle">登录您的账号
                （测试账号：admin/admin 或 guest/guest）</p>
          </div>

          <form className="auth-form" onSubmit={handleLogin}>
            <div className="form-item">
              <label className="form-label">工号</label>
              <input
                type="text"
                className="form-input"
                placeholder="请输入8位工号"
                value={loginData.employee_id}
                onChange={(e) =>
                  setLoginData({ ...loginData, employee_id: e.target.value })
                }
                disabled={loading}
              />
            </div>

            <div className="form-item">
              <label className="form-label">密码</label>
              <input
                type="password"
                className="form-input"
                placeholder="请输入密码"
                value={loginData.password}
                onChange={(e) =>
                  setLoginData({ ...loginData, password: e.target.value })
                }
                disabled={loading}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    handleLogin(e);
                  }
                }}
              />
            </div>

            <Button
              variant="primary"
              onClick={handleLogin}
              disabled={loading}
              style={{ width: "100%", marginTop: "24px" }}
            >
              {loading ? "登录中..." : "登录"}
            </Button>
          </form>

          <div className="auth-footer">
            <span>还没有账号？</span>
            <a href="#" onClick={(e) => { e.preventDefault(); setIsRegisterMode(true); }} className="auth-link">
              立即注册
            </a>
          </div>
        </div>
      ) : (
        <div className="auth-card">
          <div className="auth-header">
            <h1 className="auth-title">创建账号</h1>
            <p className="auth-subtitle">注册后系统将为您创建专属租户</p>
          </div>

          <form className="auth-form" onSubmit={handleRegister}>
            <div className="form-item">
              <label className="form-label">用户名</label>
              <input
                type="text"
                className="form-input"
                placeholder="请输入用户名"
                value={registerData.username}
                onChange={(e) =>
                  setRegisterData({ ...registerData, username: e.target.value })
                }
                disabled={loading}
              />
            </div>

            <div className="form-item">
              <label className="form-label">工号</label>
              <input
                type="text"
                className="form-input"
                placeholder="请输入8位工号"
                value={registerData.employee_id}
                onChange={(e) =>
                  setRegisterData({ ...registerData, employee_id: e.target.value })
                }
                disabled={loading}
                maxLength={8}
              />
            </div>

            <div className="form-item">
              <label className="form-label">密码</label>
              <input
                type="password"
                className="form-input"
                placeholder="请输入密码（8-32位，包含字母和数字）"
                value={registerData.password}
                onChange={(e) =>
                  setRegisterData({ ...registerData, password: e.target.value })
                }
                disabled={loading}
              />
            </div>

            <div className="form-item">
              <label className="form-label">确认密码</label>
              <input
                type="password"
                className="form-input"
                placeholder="请再次输入密码"
                value={registerData.confirmPassword}
                onChange={(e) =>
                  setRegisterData({ ...registerData, confirmPassword: e.target.value })
                }
                disabled={loading}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    handleRegister(e);
                  }
                }}
              />
            </div>

            <Button
              variant="primary"
              onClick={handleRegister}
              disabled={loading}
              style={{ width: "100%", marginTop: "24px" }}
            >
              {loading ? "注册中..." : "注册"}
            </Button>
          </form>

          <div className="auth-footer">
            <span>已有账号？</span>
            <a href="#" onClick={(e) => { e.preventDefault(); setIsRegisterMode(false); }} className="auth-link">
              返回登录
            </a>
          </div>
        </div>
      )}
    </div>
  );
};

export default Login;

