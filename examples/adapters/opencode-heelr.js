// opencode plugin: report the session's folder to heelr. Copy to ~/.config/opencode/plugins/.
export const HeelrReport = async ({ $ }) => ({
  event: async ({ event }) => {
    if (!process.env.HERDR_PANE_ID) return
    if (event.type !== "session.created" && event.type !== "session.updated") return
    const dir = event.properties?.info?.directory
    if (!dir) return
    await $`printf '%s' ${JSON.stringify({ cwd: dir, agent: "opencode" })} | heelr report`.quiet().nothrow()
  },
})
