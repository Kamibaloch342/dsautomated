import { createContext, useContext, useState, useEffect, useCallback } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => localStorage.getItem("auth_token"));
  const [loading, setLoading] = useState(true);

  // On mount, verify stored token
  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }

    fetch(`${API_BASE}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => {
        if (!res.ok) throw new Error("Invalid token");
        return res.json();
      })
      .then((data) => setUser(data.user))
      .catch(() => {
        localStorage.removeItem("auth_token");
        setToken(null);
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, [token]);

  const login = useCallback(async (email, password) => {
    const res = await fetch(`${API_BASE}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Login failed");
    localStorage.setItem("auth_token", data.token);
    setToken(data.token);
    setUser(data.user);
    return data;
  }, []);

  const register = useCallback(async (formData) => {
    const res = await fetch(`${API_BASE}/api/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formData),
    });
    const data = await res.json();
    if (!res.ok) {
      const msg = Array.isArray(data.error) ? data.error.join(" ") : data.error;
      throw new Error(msg || "Registration failed");
    }
    localStorage.setItem("auth_token", data.token);
    setToken(data.token);
    setUser(data.user);
    return data;
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem("auth_token");
    setToken(null);
    setUser(null);
  }, []);

  const updateProfile = useCallback(
    async (profileData) => {
      const res = await fetch(`${API_BASE}/api/auth/me`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(profileData),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Update failed");
      setUser(data.user);
      return data;
    },
    [token]
  );

  const value = {
    user,
    token,
    loading,
    isAuthenticated: !!user,
    login,
    register,
    logout,
    updateProfile,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
