import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      navigate("/");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="page-enter">
      <div className="auth-page">
        <div className="auth-card" id="login-card">
          <div className="auth-card__header">
            <h1 className="auth-card__title">Welcome Back</h1>
            <p className="auth-card__subtitle">
              Sign in to your ZoroStore account
            </p>
          </div>

          {error && (
            <div className="auth-card__error" id="login-error">
              {error}
            </div>
          )}

          <form className="auth-form" onSubmit={handleSubmit}>
            <div className="form-group">
              <label htmlFor="login-email" className="form-label">
                Email Address
              </label>
              <input
                id="login-email"
                type="email"
                className="form-input"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoFocus
              />
            </div>

            <div className="form-group">
              <label htmlFor="login-password" className="form-label">
                Password
              </label>
              <input
                id="login-password"
                type="password"
                className="form-input"
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
              />
            </div>

            <button
              type="submit"
              className="btn btn--accent auth-form__submit"
              disabled={loading}
              id="login-submit"
            >
              {loading ? "Signing in..." : "Sign In"}
            </button>
          </form>

          <div className="auth-card__footer">
            Don't have an account?{" "}
            <Link to="/register" className="auth-card__link">
              Create one
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}

export default LoginPage;
