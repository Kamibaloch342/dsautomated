import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();

  const [form, setForm] = useState({
    first_name: "",
    last_name: "",
    email: "",
    password: "",
    confirm_password: "",
    phone: "",
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const update = (field) => (e) =>
    setForm((prev) => ({ ...prev, [field]: e.target.value }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");

    if (form.password !== form.confirm_password) {
      setError("Passwords do not match.");
      return;
    }

    setLoading(true);
    try {
      await register(form);
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
        <div className="auth-card auth-card--wide" id="register-card">
          <div className="auth-card__header">
            <h1 className="auth-card__title">Create Account</h1>
            <p className="auth-card__subtitle">
              Join ZoroStore for fast ordering and order tracking
            </p>
          </div>

          {error && (
            <div className="auth-card__error" id="register-error">
              {error}
            </div>
          )}

          <form className="auth-form" onSubmit={handleSubmit}>
            <div className="form-row">
              <div className="form-group">
                <label htmlFor="reg-first" className="form-label">
                  First Name *
                </label>
                <input
                  id="reg-first"
                  type="text"
                  className="form-input"
                  placeholder="John"
                  value={form.first_name}
                  onChange={update("first_name")}
                  required
                  autoFocus
                />
              </div>
              <div className="form-group">
                <label htmlFor="reg-last" className="form-label">
                  Last Name *
                </label>
                <input
                  id="reg-last"
                  type="text"
                  className="form-input"
                  placeholder="Doe"
                  value={form.last_name}
                  onChange={update("last_name")}
                  required
                />
              </div>
            </div>

            <div className="form-group">
              <label htmlFor="reg-email" className="form-label">
                Email Address *
              </label>
              <input
                id="reg-email"
                type="email"
                className="form-input"
                placeholder="you@example.com"
                value={form.email}
                onChange={update("email")}
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="reg-phone" className="form-label">
                Phone (optional)
              </label>
              <input
                id="reg-phone"
                type="tel"
                className="form-input"
                placeholder="(555) 123-4567"
                value={form.phone}
                onChange={update("phone")}
              />
            </div>

            <div className="form-row">
              <div className="form-group">
                <label htmlFor="reg-password" className="form-label">
                  Password *
                </label>
                <input
                  id="reg-password"
                  type="password"
                  className="form-input"
                  placeholder="Min. 6 characters"
                  value={form.password}
                  onChange={update("password")}
                  required
                  minLength={6}
                />
              </div>
              <div className="form-group">
                <label htmlFor="reg-confirm" className="form-label">
                  Confirm Password *
                </label>
                <input
                  id="reg-confirm"
                  type="password"
                  className="form-input"
                  placeholder="Re-enter password"
                  value={form.confirm_password}
                  onChange={update("confirm_password")}
                  required
                  minLength={6}
                />
              </div>
            </div>

            <button
              type="submit"
              className="btn btn--accent auth-form__submit"
              disabled={loading}
              id="register-submit"
            >
              {loading ? "Creating Account..." : "Create Account"}
            </button>
          </form>

          <div className="auth-card__footer">
            Already have an account?{" "}
            <Link to="/login" className="auth-card__link">
              Sign In
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}

export default RegisterPage;
