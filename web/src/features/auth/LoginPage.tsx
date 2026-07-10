/**
 * LoginPage — mirrors legacy loginPage HTML (index.html:537-556) and
 * the login() function (index.html:5112-5130).
 *
 * Shows a local login form by default.  When LDAP is enabled the type-bar
 * appears (域账号 / 内建账号) mirroring loginTypeBar (index.html:542-546).
 */
import React, { useEffect, useRef, useState } from "react";
import { useAuth } from "../../api/AuthContext";

export function LoginPage() {
  const { ldapStatus, login } = useAuth();

  // "ldap" | "local" — default ldap when LDAP is enabled (matches legacy active state)
  const [loginType, setLoginType] = useState<"local" | "ldap">("local");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const userRef = useRef<HTMLInputElement>(null);

  // When LDAP becomes enabled, default to ldap tab (matches legacy ltype-bar first button)
  useEffect(() => {
    if (ldapStatus.enabled) setLoginType("ldap");
  }, [ldapStatus.enabled]);

  // Focus username on mount (mirrors requestAnimationFrame(() => $("lpUser").focus()))
  useEffect(() => {
    userRef.current?.focus();
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setError("");
    setSubmitting(true);
    try {
      const type = ldapStatus.enabled ? loginType : "local";
      await login(username.trim(), password, type);
      // On success AuthContext updates user → App re-renders to main shell
    } catch (err) {
      setError(
        err instanceof Error ? (err.message || "用户名或密码不正确") : "用户名或密码不正确",
      );
      setPassword("");
      setTimeout(() => document.getElementById("lp-pass")?.focus(), 0);
    } finally {
      setSubmitting(false);
    }
  }

  function handleUserKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") document.getElementById("lp-pass")?.focus();
  }

  function handlePassKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") void handleSubmit(e as unknown as React.FormEvent);
  }

  return (
    <div id="loginPage">
      <div className="login-box">
        <div className="brand mb-15r ta-c">
          <span className="logo">HPC</span>
          <span>发布信息协作系统</span>
        </div>

        {ldapStatus.enabled && (
          <div id="loginTypeBar" className="ltype-bar mb-1r">
            <button
              type="button"
              className={loginType === "ldap" ? "active" : ""}
              data-ltype="ldap"
              onClick={() => setLoginType("ldap")}
            >
              域账号
            </button>
            <button
              type="button"
              className={loginType === "local" ? "active" : ""}
              data-ltype="local"
              onClick={() => setLoginType("local")}
            >
              内建账号
            </button>
          </div>
        )}

        <form onSubmit={handleSubmit} noValidate>
          <div className="mb-075r">
            <input
              id="lp-user"
              ref={userRef}
              className="input"
              placeholder="请输入用户名"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              onKeyDown={handleUserKeyDown}
            />
          </div>
          <div className="mb-075r">
            <input
              id="lp-pass"
              className="input"
              type="password"
              placeholder="请输入密码"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={handlePassKeyDown}
            />
          </div>

          <button
            id="lpBtn"
            type="submit"
            className="btn primary"
            disabled={submitting}
          >
            登 录
          </button>

          {error && (
            <div id="lpError" className="lerr mt-05r">
              {error}
            </div>
          )}
        </form>
      </div>
    </div>
  );
}
