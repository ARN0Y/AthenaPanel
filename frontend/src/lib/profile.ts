// Single source of truth for the customer-facing connection profile.
//
// Both the Users list ("Copy profile") and the user detail page (ProfileCard)
// render this. Keeping one builder is what stops the two screens disagreeing —
// they previously had independent copies and only one of them learned about
// l2tp_mode, so raw users were handed the IPsec address plus the PSK.
import type { ServerSettings, User } from "@/lib/api";

export interface ProfileBlock {
  title: string;
  text: string;
}

export type ProfileSettings = Pick<
  ServerSettings,
  "server_address" | "sstp_address" | "l2tp_raw_address" | "vpn_psk" | "l2tp_enabled" | "sstp_enabled"
>;

export interface Profile {
  blocks: ProfileBlock[];
  /** Raw mode is selected but no raw entry host is configured -> no L2TP block. */
  rawUnconfigured: boolean;
}

export function isRawMode(user: Pick<User, "l2tp_mode">): boolean {
  return (user.l2tp_mode || "ipsec") === "raw";
}

export function buildProfile(user: User, s: ProfileSettings | undefined): Profile {
  if (!s) return { blocks: [], rawUnconfigured: false };

  const raw = isRawMode(user);
  const rawAddr = (s.l2tp_raw_address || "").trim();
  const rawUnconfigured = s.l2tp_enabled && raw && !rawAddr;
  const blocks: ProfileBlock[] = [];

  if (s.l2tp_enabled && !rawUnconfigured) {
    blocks.push(
      raw
        ? {
            // No pre-shared key: this endpoint carries no IPsec at all.
            title: "L2TP — no IPsec",
            text: [
              `Server_Address : ${rawAddr}`,
              `L2TP WITHOUT IPsec (no pre-shared key)`,
              `Username : ${user.username}`,
              `Password : ${user.password}`,
            ].join("\n"),
          }
        : {
            title: "L2TP/IPsec",
            text: [
              `Server_Address : ${s.server_address}`,
              `L2TP/IPsec with pre-shared key`,
              // Labelled like every other line so the admin can read the key off
              // the card directly. Never render a blank value: an empty VPN_PSK
              // would otherwise look like a key that is simply hard to see.
              `Pre-shared key : ${s.vpn_psk || "(not set — configure VPN_PSK)"}`,
              `Username : ${user.username}`,
              `Password : ${user.password}`,
            ].join("\n"),
          },
    );
  }

  if (s.sstp_enabled) {
    blocks.push({
      title: "SSTP",
      text: [
        `Server_Address : ${s.sstp_address}`,
        `SSTP (https / port 443)`,
        `Username : ${user.username}`,
        `Password : ${user.password}`,
      ].join("\n"),
    });
  }

  return { blocks, rawUnconfigured };
}

/** The whole profile as one copy-able blob (Users list row action). */
export function profileText(user: User, s: ProfileSettings | undefined): string {
  return buildProfile(user, s)
    .blocks.map((b) => b.text)
    .join("\n\n");
}
