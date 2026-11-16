#include "project_macros.h"
!> Callbacks with user data: the unlimited polymorphic argument is dropped in C++.
module solver_mod
  implicit none
  private

  public :: NonLinearFunction, SolverObserver, fixed_point_type, fixed_point_solve, residual_norm

  type :: fixed_point_type
    integer :: max_iter = 50
    real :: tol = 1.e-12
    procedure(NonLinearFunction), pointer, nopass :: monitor => null()
  contains
    procedure :: solve => fixed_point_solve_method
  end type fixed_point_type

  abstract interface
    !> residual of a nonlinear system
    subroutine NonLinearFunction(userData, x, f, ierr)
      class(*), intent(in)  :: userData    !< user data given to the solver
      real,     intent(in)  :: x(:)        !< current approximate solution vector
      real,     intent(out) :: f(:)        !< new residual vector
      integer,  intent(out) :: ierr        !< error flag
    end subroutine NonLinearFunction

    !> called after each solve
    subroutine SolverObserver(solver, iterations)
      import :: fixed_point_type
      class(fixed_point_type), intent(in) :: solver
      integer, intent(in) :: iterations
    end subroutine SolverObserver
  end interface

contains

  !> x <- x - f(x) until |f(x)| is small
  subroutine fixed_point_solve(fun, userData, x, tol, max_iter, ierr, progress)
    procedure(NonLinearFunction) :: fun
    procedure(NonLinearFunction), optional :: progress   !< called after each step
    class(*), intent(in) :: userData
    real, intent(inout) :: x(:)
    real, intent(in) :: tol
    integer, intent(in) :: max_iter
    integer, intent(out) :: ierr
    real :: f(size(x))
    integer :: it

    ierr = 0
    do it = 1, max_iter
      PROJECT_CHK_IERR(call fun(userData, x, f, ierr), ierr)
      if (residual_norm(fun, userData, x) < tol) exit
      x = x - f
      if (present(progress)) call progress(userData, x, f, ierr)
    end do
  end subroutine fixed_point_solve

  function residual_norm(fun, userData, x) result(r)
    procedure(NonLinearFunction) :: fun
    class(*), intent(in) :: userData
    real, intent(in) :: x(:)
    real :: r
    real :: f(size(x))
    integer :: ierr
    call fun(userData, x, f, ierr)
    r = maxval(abs(f))
  end function residual_norm

  subroutine fixed_point_solve_method(this, fun, userData, x, ierr)
    class(fixed_point_type), intent(inout) :: this
    procedure(NonLinearFunction) :: fun
    class(*), intent(in) :: userData
    real, intent(inout) :: x(:)
    integer, intent(out) :: ierr
    real :: f(size(x))

    PROJECT_CHK_IERR(call fixed_point_solve(fun, userData, x, this%tol, this%max_iter, ierr), ierr)
    if (associated(this%monitor)) then
      PROJECT_CHK_IERR(call this%monitor(userData, x, f, ierr), ierr)
    end if
  end subroutine fixed_point_solve_method

end module solver_mod
