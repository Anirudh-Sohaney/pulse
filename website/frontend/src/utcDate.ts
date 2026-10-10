/** Keep public source dates in the UTC calendar used by the API. */
export function utcDate(value: string): string {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : `${parsed.toISOString().slice(0, 10)} UTC`
}

export function utcDateTime(value: string): string {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime())
    ? value
    : `${parsed.toISOString().slice(0, 16).replace('T', ' ')} UTC`
}
