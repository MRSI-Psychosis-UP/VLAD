args <- commandArgs(trailingOnly = FALSE)
script_arg <- grep("^--file=", args, value = TRUE)
if (length(script_arg) != 1L) {
  stop("Run this installer with Rscript /path/to/VLAD/install_R_packages.R")
}
project_dir <- dirname(normalizePath(sub("^--file=", "", script_arg)))
source(file.path(project_dir, "requirements.R"))

# Keep report dependencies separate from the user's global R installation.
library_dir <- Sys.getenv(
  "VLAD_R_LIBRARY",
  unset = file.path(project_dir, ".local", "R-library")
)
dir.create(library_dir, recursive = TRUE, showWarnings = FALSE)
library_dir <- normalizePath(library_dir, mustWork = TRUE)
if (file.access(library_dir, 2L) != 0L) {
  stop("R library is not writable: ", library_dir)
}
.libPaths(c(library_dir, .Library.site, .Library))
Sys.setenv(R_LIBS_USER = library_dir)

repos <- getOption("repos")
if (is.null(repos) || identical(unname(repos["CRAN"]), "@CRAN@")) {
  repos <- c(CRAN = "https://cloud.r-project.org")
}

# Reuse project/system packages, excluding user libraries hidden at runtime.
installed <- vapply(vlad_r_packages, requireNamespace, logical(1), quietly = TRUE)
missing <- vlad_r_packages[!installed]

if (length(missing) == 0) {
  message("All VLAD R packages are already installed.")
} else {
  cores <- parallel::detectCores(logical = TRUE)
  if (is.na(cores)) cores <- 1L
  install_cpus <- as.integer(Sys.getenv("VLAD_R_INSTALL_CPUS", max(1L, cores - 1L)))
  if (is.na(install_cpus) || install_cpus < 1L) {
    stop("VLAD_R_INSTALL_CPUS must be a positive integer")
  }
  install.packages(
    missing,
    lib = library_dir,
    repos = repos,
    Ncpus = install_cpus
  )
}

# install.packages can merely warn on compilation failure; setup must fail.
available <- vapply(vlad_r_packages, requireNamespace, logical(1), quietly = TRUE)
if (!all(available)) {
  stop("R packages could not be loaded: ", paste(vlad_r_packages[!available], collapse = ", "))
}
message("VLAD R library: ", library_dir)
