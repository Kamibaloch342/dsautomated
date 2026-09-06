import { useState, useEffect } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { AlertIcon, CheckCircle, MailIcon } from "../components/Icons";
import { BRAND } from "../lib/brand";

function ForgotPasswordPage() {
  const { forgotPassword, resetPassword } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const queryToken = searchParams.get("token") || "";
  const queryEmail = searchParams.get("email") || "";

  const [step, setStep] = useState(queryToken ? "reset" : "request"); // "request" | "reset" | "success"
  const [email, setEmail] = useState(queryEmail);
  const [otp, setOtp] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (queryToken && queryEmail) {
      setEmail(queryEmail);
      setStep("reset");
      setInfo("Enter your new password below.");
    }
  }, [queryToken, queryEmail]);

  // Request password reset code
  const handleRequestCode = async (e) => {
    e?.preventDefault();
    setError("");
    setInfo("");
    if (!email || !email.includes("@")) {
      setError("Please enter a valid email address.");
      return;
    }
    setLoading(true);
    try {
      await forgotPassword(email);
      setInfo(`Password reset code sent to ${email}`);
      setStep("reset");
    } catch (err) {
      setError(err.message || "Failed to send reset code.");
    } finally {
      setLoading(false);
    }
  };

  // Submit new password
  const handleResetPassword = async (e) => {
    e.preventDefault();
    setError("");

    if (!queryToken && (!otp || otp.length < 6)) {
      setError("Please enter the 6-digit reset code sent to your email.");
      return;
    }
    if (newPassword.length < 6) {
      setError("Password must be at least 6 characters long.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setLoading(true);
    try {
      await resetPassword({
        email,
        otp: queryToken ? "" : otp,
        token: queryToken || null,
        newPassword,
      });
      setStep("success");
    } catch (err) {
      setError(err.message || "Failed to reset password.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="z-page z-container">
      <div className="auth-wrap">
        <div className="z-card" id="forgot-password-card">
          <h1>Reset Password</h1>
          <p className="sub">
            {step === "request"
              ? "Enter your account email to receive a 6-digit recovery code."
              : step === "reset"
              ? "Set your new account password."
              : "Your password has been updated successfully!"}
          </p>

          {error && (
            <div className="z-alert z-alert--error mb-4">
              <AlertIcon size={16} />
              <span>{error}</span>
            </div>
          )}

          {info && (
            <div className="z-alert z-alert--success mb-4 flex items-center gap-2 text-xs font-semibold text-emerald-800 bg-emerald-50 border border-emerald-200 p-3 rounded-lg">
              <CheckCircle size={16} className="text-emerald-600" />
              <span>{info}</span>
            </div>
          )}

          {step === "request" && (
            <form onSubmit={handleRequestCode}>
              <div className="z-field">
                <label htmlFor="reset-email">Account Email address</label>
                <input
                  id="reset-email"
                  type="email"
                  className="z-input"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoFocus
                />
              </div>

              <button
                type="submit"
                className="z-btn z-btn--primary z-btn--block"
                disabled={loading}
              >
                {loading ? "Sending Code…" : "Send Recovery Code →"}
              </button>
            </form>
          )}

          {step === "reset" && (
            <form onSubmit={handleResetPassword}>
              <div className="z-field">
                <label htmlFor="reset-email-display">Email</label>
                <input
                  id="reset-email-display"
                  type="email"
                  className="z-input bg-slate-100 cursor-not-allowed"
                  value={email}
                  disabled
                />
              </div>

              {!queryToken && (
                <div className="z-field text-center">
                  <label htmlFor="reset-otp" className="block text-xs font-bold text-slate-700 mb-2">
                    6-Digit Code from your Email
                  </label>
                  <input
                    id="reset-otp"
                    type="text"
                    maxLength={6}
                    className="z-input text-center text-2xl font-mono tracking-widest font-black"
                    placeholder="123456"
                    value={otp}
                    onChange={(e) => setOtp(e.target.value.replace(/\\D/g, ""))}
                    required
                    autoFocus
                  />
                </div>
              )}

              <div className="z-field">
                <label htmlFor="new-password">New Password</label>
                <input
                  id="new-password"
                  type="password"
                  className="z-input"
                  placeholder="At least 6 characters"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  required
                  minLength={6}
                />
              </div>

              <div className="z-field">
                <label htmlFor="confirm-password">Confirm New Password</label>
                <input
                  id="confirm-password"
                  type="password"
                  className="z-input"
                  placeholder="Re-enter new password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                  minLength={6}
                />
              </div>

              <button
                type="submit"
                className="z-btn z-btn--primary z-btn--block mt-4"
                disabled={loading}
              >
                {loading ? "Saving…" : "Save New Password & Sign In"}
              </button>

              {!queryToken && (
                <div className="flex justify-between items-center mt-4 text-xs">
                  <button
                    type="button"
                    onClick={() => setStep("request")}
                    className="text-slate-500 hover:text-slate-800 underline"
                  >
                    Back
                  </button>
                  <button
                    type="button"
                    onClick={handleRequestCode}
                    disabled={loading}
                    className="text-brand-orange font-bold hover:underline"
                  >
                    Resend Code
                  </button>
                </div>
              )}
            </form>
          )}

          {step === "success" && (
            <div className="text-center py-4">
              <div className="w-12 h-12 bg-emerald-100 text-emerald-600 rounded-full flex items-center justify-center mx-auto mb-3">
                <CheckCircle size={28} />
              </div>
              <h3 className="text-lg font-bold text-slate-900 mb-2">Password Reset Complete</h3>
              <p className="text-xs text-slate-600 mb-6">
                You are now logged in. You can manage your account and continue shopping.
              </p>
              <button
                type="button"
                onClick={() => navigate("/")}
                className="z-btn z-btn--primary z-btn--block"
              >
                Go to Storefront →
              </button>
            </div>
          )}
        </div>

        <p className="auth-alt">
          Remember your password? <Link to="/login">Sign in</Link>
        </p>
      </div>
    </div>
  );
}

export default ForgotPasswordPage;
