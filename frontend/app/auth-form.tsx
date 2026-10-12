"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";

type AuthMode = "login" | "signup";

export default function AuthForm({ mode }: { mode: AuthMode }) {
  const isSignup = mode === "signup";
  const router = useRouter();
  const [passwordVisible, setPasswordVisible] = useState(false);

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    router.push("/dashboard");
  }

  return <main className="auth-shell">
    <section className="auth-visual">
      <Link className="landing-brand auth-brand" href="/"><span className="landing-brand-mark">◉</span>ASTRA<span className="brand-reg">®</span></Link>
      <div className="auth-visual-content"><span className="auth-orbit-icon">◉</span><p className="auth-eyebrow">MISSION INTELLIGENCE, IN VIEW</p><h1>Bring the<br />important<br /><em>things home.</em></h1><p className="auth-visual-copy">A clearer view of spacecraft health, data decisions and the next chance to connect.</p></div>
      <div className="auth-visual-foot"><span>SPACECRAFT TO EARTH</span><span>MISSION SYSTEMS · 01</span></div>
    </section>
    <section className="auth-side"><div className="auth-top-link"><span>ASTRA MISSION CONTROL</span><Link href="/">← Back to home</Link></div><motion.div className="auth-card" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45 }}>
      <span className="auth-card-kicker">{isSignup ? "YOUR MISSION STARTS HERE" : "WELCOME BACK, OPERATOR"}</span>
      <h2>{isSignup ? "Create your account" : "Good to see you again."}</h2>
      <p className="auth-subtitle">{isSignup ? "Set up your ground-control access." : "Sign in to return to mission control."}</p>
      <form className="auth-form" onSubmit={handleSubmit}>
        {isSignup && <label>Your name<input autoComplete="name" name="name" placeholder="Alex Morgan" required /></label>}
        <label>Work email<input autoComplete="email" name="email" type="email" placeholder="you@organization.com" required /></label>
        <label>Password<span className="password-field"><input autoComplete={isSignup ? "new-password" : "current-password"} name="password" type={passwordVisible ? "text" : "password"} placeholder={isSignup ? "At least 8 characters" : "Enter your password"} minLength={8} required /><button type="button" onClick={() => setPasswordVisible((visible) => !visible)}>{passwordVisible ? "HIDE" : "SHOW"}</button></span></label>
        {!isSignup && <div className="auth-options"><label className="remember-option"><input type="checkbox" name="remember" /> <span>Remember this device</span></label><a href="mailto:support@astra.example?subject=Password%20help">Forgot password?</a></div>}
        <button className="auth-submit" type="submit">{isSignup ? "Create account" : "Log in to mission control"}<span>↗</span></button>
      </form>
      <div className="auth-switch">{isSignup ? "Already have access?" : "New to Astra?"} <Link href={isSignup ? "/login" : "/signup"}>{isSignup ? "Log in" : "Create an account"}</Link></div>
      <p className="auth-demo-note"><i /> Frontend preview — submitting this form opens the demo dashboard. No account is created.</p>
    </motion.div><div className="auth-bottom-note"><span>SAFETY-FIRST AUTONOMY</span><span>ENCRYPTED LINK · OPERATOR CONTROL</span></div></section>
  </main>;
}
