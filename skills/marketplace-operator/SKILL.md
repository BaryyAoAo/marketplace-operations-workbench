---
name: marketplace-operator
description: Prepare English Los Angeles Marketplace listing or buyer-reply drafts from the local Marketplace Workbench product facts through its MCP tools. Use for this project's drafting and review workflow, not for publishing or sending.
---

# Marketplace operator

Use the Marketplace Workbench MCP server from this repository's `mcp_server.py`. Its tools read and write the same SQLite state as the local web console. Install the project dependencies and register the server from your local checkout before using this skill.

## Listing copy

1. Call `get_product` for the requested product ID. If the product or its source facts are missing, report what needs confirmation.
2. Draft an English title and description for the product's market (new samples target Los Angeles) using only the returned name, condition, material, size, color, and category. Leave the price to the structured UI field. Do not infer gemstone authenticity, metal purity, certifications, warranties, delivery terms, or discounts.
3. Call `save_listing_draft`, then `review_listing_draft`. If it reports issues, revise by creating a new draft version or tell the user what source fact must be confirmed. Identify the saved draft as *pending human approval*.

## Buyer replies

1. Call `get_inquiry`. Do not copy buyer contact details into the draft; ask for a redacted question if such details are present.
2. Answer from the linked product facts. Use the exact structured price and currency when relevant. Say that delivery, payment, or other missing policies need human confirmation.
3. Call `save_reply_draft`. Treat `saved: false` or any review issue as a blocked draft. Identify a saved reply as *pending human approval*.

The MCP server has no Facebook or WhatsApp publishing or send tool. Do not describe saved drafts as published or sent. The human operator reviews and records those steps in the web console.
