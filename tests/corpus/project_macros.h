#define PROJECT_CHK_IERR(stmt, ierr) stmt; if (ierr /= 0) return
#define PROJECT_ALLOCATE(arr, ierr) allocate(arr, stat=ierr); if (ierr /= 0) return
#define PROJECT_ASSERT(cond, ierr) if (.not. (cond)) then; ierr = -1; return; end if
#define PROJECT_CHK_ARGUMENT(cond, ierr) if (.not. (cond)) then; ierr = -2; return; end if
