// Main page: the flip-disk clock in the middle of the screen (Korea time; reads no trading data).
import { FlipDiskMatrix } from "@/components/flip-disk-matrix";

export function ClockPage() {
  return (
    <section className="dark clockpage" aria-label="시계">
      <div className="mono clocklabel">한국시간 (KST)</div>
      <FlipDiskMatrix />
    </section>
  );
}
