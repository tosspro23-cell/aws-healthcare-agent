import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Port 8765 is not arbitrary: it's the exact redirect URI
// (http://localhost:8765/callback) already registered on the Cognito App
// Client in infra/stacks/auth_stack.py, the same one infra/scripts/get_dev_token.py
// uses. Running the dev server on a different port would need a matching
// CDK change (and redeploy) to add another allowed callback URL first.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 8765,
    strictPort: true,
  },
});
