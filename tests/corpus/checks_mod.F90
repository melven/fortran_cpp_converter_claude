! checks_mod: argument checks and assertions (negated conditions in C++)
#include "project_macros.h"
module checks_mod
  implicit none
contains
  !> checks of the arguments
  subroutine check(n, m, x, f, ierr)
    integer, intent(in) :: n        !< count
    integer, intent(in) :: m        !< other count
    real, intent(in) :: x           !< value
    logical, intent(in) :: f        !< flag
    integer, intent(out) :: ierr    !< error flag
    ierr = 0
    PROJECT_CHK_ARGUMENT(n >= 0, ierr)
    PROJECT_CHK_ARGUMENT(n >= 0 .and. (m < n .or. .not. f), ierr)
    PROJECT_CHK_ARGUMENT(x >= 0.0, ierr)
    PROJECT_CHK_ARGUMENT(x == 0.0 .or. n /= 1, ierr)
    PROJECT_ASSERT(f, ierr)
    PROJECT_ASSERT(.true., ierr)
    PROJECT_ASSERT_MSG(n > m, "n > m", ierr)
    if (n > 100) then
      PROJECT_ASSERT(.false., ierr)
    end if
  end subroutine check
end module checks_mod
