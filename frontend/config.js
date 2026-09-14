// Backend API base URL.
// Local dev (served from localhost) hits the local FastAPI server.
// Any other host (e.g. the deployed Netlify site) hits the deployed backend.
window.API_BASE =
  window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
    ? "http://localhost:8000"
    : "https://ai-resume-screener-plum.vercel.app";
