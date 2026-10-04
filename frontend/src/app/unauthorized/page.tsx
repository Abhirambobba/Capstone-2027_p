import Link from "next/link";

export default function UnauthorizedPage() {
  return <main className="login-page"><section className="login-panel"><p className="eyebrow">Astra workspace</p><h1>Access denied</h1><p>Your account is not authorized to use this page.</p><Link className="button secondary" href="/login">Return to sign in</Link></section></main>;
}