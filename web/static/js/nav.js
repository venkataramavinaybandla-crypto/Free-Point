import { api } from "./api.js";
import { el } from "./dom.js";

const LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/approvals", label: "Approvals" },
  { href: "/settings", label: "Settings" },
  { href: "/docs", label: "API docs" },
];

function signOut() {
  return el("button", {
    class: "btn btn-ghost",
    type: "button",
    text: "Sign out",
    onclick: async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await api("/auth/logout", { method: "POST" });
      } catch {
        // A missing session still ends at the login page.
      }
      window.location.assign("/login");
    },
  });
}

export function renderNav({ authed }) {
  const host = document.getElementById("site-nav");
  if (!host) return;

  const brand = el(
    "a",
    { class: "brand", href: "/" },
    [
      el("img", {
        class: "brand-mark",
        src: "/static/assets/header_image.png",
        alt: "Free Point",
      }),
      el("span", { class: "brand-name", text: "Free Point" }),
    ]
  );

  const links = el(
    "nav",
    { class: "nav-links", "aria-label": "Primary" },
    LINKS.map((item) =>
      el("a", {
        class: "nav-link",
        href: item.href,
        text: item.label,
        "aria-current": window.location.pathname === item.href ? "page" : null,
      })
    )
  );

  const actions = el(
    "div",
    { class: "nav-actions" },
    authed
      ? [signOut()]
      : [
          el("a", { class: "btn btn-ghost", href: "/login", text: "Sign in" }),
          el("a", { class: "btn btn-primary", href: "/signup", text: "Get a key" }),
        ]
  );

  host.append(
    el("div", { class: "nav-capsule" }, [
      el("div", { class: "nav-streak", "aria-hidden": "true" }),
      brand,
      links,
      actions,
    ])
  );
}
