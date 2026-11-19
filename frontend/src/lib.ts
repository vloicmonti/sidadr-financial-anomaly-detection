import { clsx, type ClassValue } from "clsx";

export function cn(...values: ClassValue[]): string {
  return clsx(values);
}

const number = new Intl.NumberFormat("fr-FR");

export function formatNumber(value: number): string {
  return number.format(value);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function formatPercent(value: number): string {
  return `${new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1, minimumFractionDigits: 1 }).format(100 * value)} %`;
}
