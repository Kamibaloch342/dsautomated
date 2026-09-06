import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { MailIcon, ClipboardIcon, CheckCircleIcon, TruckIcon } from "../components/Icons";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";

const CATEGORIES = [
  ["order_issue", "Problem with an order"],
  ["return", "Return / replacement"],
  ["shipping", "Shipping question"],
  ["billing", "Billing question"],
  ["product_question", "Product question"],
  ["other", "Something else"],
];

function ContactPage() {
  const { user, token, isAuthenticated } = useAuth();
  const [searchParams] = useSearchParams();
  const orderParam = searchParams.get("order") || "";
  const [form, setForm] = useState({
    name: "",
    email: "",
    category: orderParam ? "order_issue" : "other",
    order_id: orderParam,
    subject: orderParam ? `Issue with order #${orderParam}` : "",
    message: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  const update = (field) => (e) =>
    setForm((prev) => ({ ...prev, [field]: e.target.value }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const headers = { "Content-Type": "application/json" };
      if (token) headers.Authorization = `Bearer ${token}`;
      const res = await fetch(`${API_BASE}/api/cases`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          ...form,
          name: form.name || `${user?.first_name || ""} ${user?.last_name || ""}`.trim(),
          email: form.email || user?.email || "",
          order_id: form.order_id || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not open the case");
      setResult(data.case);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="page-enter">
      <section className="static-page" id="contact-page">
        <div className="container">
          <nav className="breadcrumb">
            <Link to="/">Home</Link>
            <span className="breadcrumb__sep">›</span>
            <span>Contact</span>
          </nav>

          <h1 className="static-page__title">Contact Us</h1>

          <div className="static-page__content">
            <p>
              Open a support case below — it goes straight to our team's case
              queue, you get email updates, and{" "}
              {isAuthenticated
                ? "you can follow the whole conversation from your account."
                : "signing in first lets you follow the conversation from your account."}
            </p>

            {result ? (
              <div className="case-success" id="case-success">
                <CheckCircleIcon size={30} />
                <h3>Case #{result.case_id} opened</h3>
                <p>
                  "{result.subject}" — our team will reply to{" "}
                  <strong>{result.email}</strong> shortly.
                </p>
                {isAuthenticated && (
                  <Link to="/account?tab=cases" className="btn btn--primary">
                    Track it in My Account
                  </Link>
                )}
              </div>
            ) : (
              <form className="case-form" onSubmit={handleSubmit} id="case-form">
                <h3 className="case-form__title">
                  <ClipboardIcon size={17} /> Open a support case
                </h3>

                {error && <div className="auth-card__error">{error}</div>}

                <div className="form-row">
                  <div className="form-group">
                    <label className="form-label">Your name</label>
                    <input
                      className="form-input"
                      value={form.name}
                      onChange={update("name")}
                      placeholder={user ? `${user.first_name} ${user.last_name}` : ""}
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Email *</label>
                    <input
                      type="email"
                      className="form-input"
                      value={form.email}
                      onChange={update("email")}
                      placeholder={user?.email || "you@company.com"}
                      required={!user}
                    />
                  </div>
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label className="form-label">Topic</label>
                    <select
                      className="form-input"
                      value={form.category}
                      onChange={update("category")}
                    >
                      {CATEGORIES.map(([value, label]) => (
                        <option key={value} value={value}>{label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="form-group">
                    <label className="form-label">Order # (optional)</label>
                    <input
                      className="form-input"
                      value={form.order_id}
                      onChange={update("order_id")}
                      placeholder="e.g. 6"
                    />
                  </div>
                </div>

                <div className="form-group">
                  <label className="form-label">Subject *</label>
                  <input
                    className="form-input"
                    value={form.subject}
                    onChange={update("subject")}
                    required
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Message *</label>
                  <textarea
                    className="form-input case-form__message"
                    rows={5}
                    value={form.message}
                    onChange={update("message")}
                    required
                  />
                </div>

                <button
                  type="submit"
                  className="btn btn--accent"
                  disabled={submitting}
                  id="case-submit"
                >
                  {submitting ? "Opening case…" : "Open support case"}
                </button>
              </form>
            )}

            <div className="contact-grid">
              <div className="contact-card">
                <div className="contact-card__icon"><MailIcon size={22} /></div>
                <h3>Email</h3>
                <p>
                  <a href="mailto:support@zorostore.com">support@zorostore.com</a>
                </p>
                <p className="contact-card__note">
                  We typically respond within 24 hours.
                </p>
              </div>

              <div className="contact-card">
                <div className="contact-card__icon"><ClipboardIcon size={22} /></div>
                <h3>Phone</h3>
                <p>
                  <a href="tel:+18001234567">1-800-123-4567</a>
                </p>
                <p className="contact-card__note">Mon–Fri, 8am–6pm EST</p>
              </div>

              <div className="contact-card">
                <div className="contact-card__icon"><TruckIcon size={22} /></div>
                <h3>Office</h3>
                <p>123 Industrial Blvd</p>
                <p>Suite 100, Chicago, IL 60601</p>
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

export default ContactPage;
