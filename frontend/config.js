// Backend API base URL.
// Local dev (served from localhost) hits the local FastAPI server.
// Any other host (e.g. the deployed Netlify site) hits the deployed backend.
// Replace the placeholder below with your Render service URL after deploying the backend.
window.API_BASE =
  window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
    ? "http://localhost:8000"
    : "https://REPLACE_WITH_YOUR_RENDER_URL.onrender.com";
