import { handleRefresh } from "./handler.ts";

Deno.serve((req) =>
  handleRefresh(req, {
    token: Deno.env.get("DASHBOARD_GH_TOKEN") ?? "",
  })
);
