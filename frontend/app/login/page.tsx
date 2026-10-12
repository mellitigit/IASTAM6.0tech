import type { Metadata } from "next";
import AuthForm from "../auth-form";

export const metadata: Metadata = { title: "Log in — Astra Mission Control" };

export default function LoginPage() {
  return <AuthForm mode="login" />;
}
