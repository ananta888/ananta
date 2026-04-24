if exists("g:loaded_ananta_plugin")
  finish
endif
let g:loaded_ananta_plugin = 1

lua << EOF
require("ananta").setup()
EOF

command! -nargs=* AnantaGoalSubmit lua require("ananta").goal_submit(<q-args>)
command! -nargs=0 AnantaAnalyze lua require("ananta").analyze()
command! -range -nargs=0 AnantaReview lua require("ananta").review()
command! -nargs=0 AnantaPatchPlan lua require("ananta").patch_plan()
command! -nargs=* AnantaProjectNew lua require("ananta").project_new(<q-args>)
command! -nargs=* AnantaProjectEvolve lua require("ananta").project_evolve(<q-args>)
command! -nargs=0 AnantaContextInspect lua require("ananta").inspect_context()
