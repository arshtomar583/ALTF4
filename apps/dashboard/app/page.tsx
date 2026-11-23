import { CheckoutMonitor } from "./checkout-monitor";

export default function Home() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-8 sm:px-8 sm:py-20">
      <header className="mb-6 sm:mb-10">
        <p className="text-sm font-semibold tracking-widest text-orange-300">PHOENIX</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-5xl">Checkout monitor</h1>
      </header>
      <CheckoutMonitor />
    </main>
  );
}
