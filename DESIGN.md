---
version: alpha
name: RetailOS
colors:
  background: "#f6f7f9"
  surface: "#ffffff"
  secondary: "#f2f4f7"
  divider: "#e5e7eb"
  input-border: "#cbd1db"
  ink: "#101828"
  muted: "#667085"
  primary: "#2e6bff"
  primary-hover: "#1e56e0"
  primary-soft: "#e9efff"
typography:
  body:
    fontFamily: "Inter, Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "14.5px"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  surface: "12px"
  control: "8px"
spacing:
  rhythm: "16px"
  panel: "20px"
  content: "28px"
---

# RetailOS interface

## Overview

RetailOS serves convenience-store staff, warehouse teams and managers in the Philippines. Preserve actual data, freshness warnings, role restrictions and ordering workflows.

The October 2026 user instruction supersedes the earlier Dribbble redesign request: improve alignment and minimalism within the current system. Retain existing navigation, colours and page structure. Use quiet surfaces, compact rectangular controls and consistent spacing; avoid a new visual identity, decorative gradients and heavy shadows.

## Colors

Runtime CSS is canonical (ownership model B). `app/web/static/workspace.css` owns tokens over `style.css` and `chat_and_charts.css`; frontmatter mirrors accepted runtime values. Background maps to `--bg`, secondary to `--surface2`, divider to `--line`, input-border to `--line2`, ink to `--ink`, muted to `--mute`, and primary colours to `--primary`, `--primary-h`, `--primary-soft`. Status colours retain their distinct semantic meanings with text. Brand lime remains on the R mark; AI actions use solid purple.

## Typography

Inter retains system fallbacks and Chinese system glyph support. Tables and metrics use tabular numbers. Sentence-case labels reduce visual noise. Georgia is limited to the R brand mark. Important amounts must remain readable at narrow widths.

## Layout

The existing expandable desktop sidebar remains. Content has a 1500px maximum width; panel, table and filter edges align to the shared content grid. Dashboard panels stretch equally and metric cards use consistent padding and gaps.

Filter inputs and actions share a baseline. Standard controls are 38px high, compact table actions are 32px, and mobile primary/icon actions are at least 44px. Tables scroll locally. Inspection lists retain mobile cards with hidden column headers. Phone sales details stack above the chart to prevent number collisions. Mobile navigation retains labels, Escape dismissal and focus restoration.

## Elevation & Depth

Cards use fine borders and near-flat shadows. Stronger elevation is reserved for dialogs and drawers. Persistent inline success/error feedback does not disappear and shift the page during reading.

## Shapes

Surfaces use `--r` (12px), normal buttons and fields use `--r2` (8px), compact actions use 6px. Status badges, avatars and progress tracks stay rounded. Native checkbox/radio controls retain keyboard semantics, 17px geometry and aligned labels.

## Components

`base.html` owns the shell, localized confirmation dialog and feedback. `app.js` owns inline validation, clear-search controls, label association, sorting keyboard behaviour and chart motion. Native select/date popups intentionally keep their platform behaviour. See `UX-CONTRACT.md`.

Charts display actual values with precise tooltips and keyboard focus. Bars reveal on visibility, lines draw smoothly, and the endpoint halo briefly pulses. Chart switching replays the relevant reveal. Reduced motion skips effects and cancels active animations. Print uses final geometry. Motion does not fabricate data or imply continuous updates. Dashboard sales remain the last 14 complete days; yesterday stays explicitly labelled.

## Do's and Don'ts

Use shared controls and tokens across pages. Preserve CSRF protection, permissions and source-of-truth rules. Provide visible focus, honest empty states and persistent failures. Do not invent trends, targets or live-sync claims. Use app-owned confirmation for destructive actions. Do not apply the abandoned reference redesign.
