source("requirements.R")

repos <- getOption("repos")
if (is.null(repos) || identical(unname(repos["CRAN"]), "@CRAN@")) {
  repos <- c(CRAN = "https://cloud.r-project.org")
}

installed <- rownames(installed.packages())
missing <- setdiff(vlad_r_packages, installed)

if (length(missing) == 0) {
  message("All VLAD R packages are already installed.")
} else {
  install.packages(missing, repos = repos)
}
