/** Countries offered as a flag for an egress location.
 *
 * A short, opinionated list rather than all 249: these are the places an
 * operator actually rents a server, and a 249-row dropdown is worse than a
 * 30-row one for every real use. "None" is always available — the flag is
 * decoration, not data anything depends on.
 *
 * Codes are ISO 3166-1 alpha-2 lowercase, which is what the API stores.
 */
export interface Country {
  code: string;
  name: string;
}

export const COUNTRIES: Country[] = [
  { code: "de", name: "Germany" },
  { code: "nl", name: "Netherlands" },
  { code: "fr", name: "France" },
  { code: "gb", name: "United Kingdom" },
  { code: "fi", name: "Finland" },
  { code: "se", name: "Sweden" },
  { code: "ch", name: "Switzerland" },
  { code: "at", name: "Austria" },
  { code: "pl", name: "Poland" },
  { code: "ro", name: "Romania" },
  { code: "it", name: "Italy" },
  { code: "es", name: "Spain" },
  { code: "tr", name: "Türkiye" },
  { code: "ru", name: "Russia" },
  { code: "ua", name: "Ukraine" },
  { code: "ae", name: "United Arab Emirates" },
  { code: "qa", name: "Qatar" },
  { code: "sa", name: "Saudi Arabia" },
  { code: "om", name: "Oman" },
  { code: "kw", name: "Kuwait" },
  { code: "bh", name: "Bahrain" },
  { code: "ir", name: "Iran" },
  { code: "az", name: "Azerbaijan" },
  { code: "am", name: "Armenia" },
  { code: "ge", name: "Georgia" },
  { code: "kz", name: "Kazakhstan" },
  { code: "in", name: "India" },
  { code: "sg", name: "Singapore" },
  { code: "jp", name: "Japan" },
  { code: "kr", name: "South Korea" },
  { code: "hk", name: "Hong Kong" },
  { code: "us", name: "United States" },
  { code: "ca", name: "Canada" },
  { code: "br", name: "Brazil" },
  { code: "au", name: "Australia" },
  { code: "za", name: "South Africa" },
];

/** ISO code -> flag emoji.
 *
 * Regional indicator symbols sit at U+1F1E6 ('A') so the code maps by offset;
 * there is no table to keep up to date. Returns "" for an unknown or empty
 * code so callers can render it unconditionally.
 */
export function flagOf(code: string | null | undefined): string {
  const c = (code || "").trim().toLowerCase();
  if (c.length !== 2 || !/^[a-z]{2}$/.test(c)) return "";
  return String.fromCodePoint(
    ...[...c].map((ch) => 0x1f1e6 + (ch.charCodeAt(0) - 97)),
  );
}

export function countryName(code: string | null | undefined): string {
  const c = (code || "").trim().toLowerCase();
  return COUNTRIES.find((x) => x.code === c)?.name ?? "";
}
