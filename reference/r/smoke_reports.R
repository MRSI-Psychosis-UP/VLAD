# Integration smoke checks for the existing R environment, not numerical parity.
# From a checkout: Rscript reference/r/smoke_reports.R
# In the image: docker run --rm -i --entrypoint Rscript vlad:r-reference - < reference/r/smoke_reports.R

script_arg <- grep("^--file=", commandArgs(FALSE), value = TRUE)
script_file <- sub("^--file=", "", script_arg)
project_dir <- if (length(script_file) == 1L && script_file != "-") {
  dirname(dirname(dirname(normalizePath(script_file))))
} else {
  getwd()
}
library_dir <- Sys.getenv("VLAD_R_LIBRARY", file.path(project_dir, ".local", "R-library"))
.libPaths(c(library_dir, .libPaths()))

smoke_report <- function(kind) {
  original_dir <- getwd()
  fixture <- tempfile(paste0("vlad-", kind, "-smoke-"))
  dir.create(fixture)
  on.exit({setwd(original_dir); unlink(fixture, recursive = TRUE)})
  setwd(fixture)
  template <- if (kind == "swe") "SWE_sum_up_v1.5.2.Rmd" else "Randomise_sum_up_v3.Rmd"
  stopifnot(file.copy(file.path(project_dir, "reference", "r", template), "report.Rmd"))
  set.seed(42)
  score <- if (kind == "swe") rnorm(30) else seq_len(30)
  design <- if (kind == "swe") {
    data.frame(subject = rep(sprintf("S%02d", 1:10), each = 3),
               visit = rep(1:3, 10), Intercept = 1, Score = score)
  } else {
    data.frame(Intercept = 1, Score = score)
  }
  write.table(design, "Model.csv", sep = "\t", quote = FALSE, row.names = FALSE)
  values <- score * 0.4 + rnorm(30, sd = 0.2)
  if (kind == "swe") {
    values <- values + rep(rnorm(10), each = 3)
    # The existing parser requires multiline contrast data; see the migration plan.
    writeLines(c("/NumWaves 2", "/NumContrasts 2", "/Matrix", "0 1", "0 -1"), "Model.con")
  }
  means <- "meants__Smoke__NAA__Model__signal__grey__contrast1.txt"
  writeLines(as.character(values), means)
  writeLines(means, "To_analyse_with_R_Smoke_Model.txt")
  writeLines("Score", "main_variable_Model.txt")
  for (name in c("Model_visualisation.png", "Model_correlation_matrix.png",
                 "NAA__Model__signal__grey__contrast1.png")) {
    png(name, width = 400, height = 300)
    plot(score, values, main = "Synthetic setup check")
    dev.off()
  }
  report_params <- list(analysis_name = "Smoke", matrix_name = "Model",
                        number_permut = "500", savepath = fixture)
  if (kind == "swe") report_params$file_type <- "mrsi"
  rmarkdown::render("report.Rmd", output_file = "smoke.html", params = report_params, quiet = TRUE)
  plot_pattern <- if (kind == "swe") "_lme[.]svg$" else "[.]svg$"
  stopifnot(file.exists("smoke.html"), file.info("smoke.html")$size > 1000,
            length(list.files(pattern = "[.]feather$")) == 1L,
            length(list.files(pattern = plot_pattern)) == 1L)
  cat(kind, "report: HTML, Feather and SVG exports passed.\n")
}

smoke_report("randomise")
smoke_report("swe")
