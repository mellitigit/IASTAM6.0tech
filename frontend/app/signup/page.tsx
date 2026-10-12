import type { Metadata } from "next";
import AuthForm from "../auth-form";

export const metadata: Metadata = { title: "Create your account — Astra Mission Control" };

export default function SignupPage() {
  return <AuthForm mode="signup" />;
}
