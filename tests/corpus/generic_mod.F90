!> Generic bindings (overloads) of a type.
module generic_mod
  implicit none
  private
  public :: solver_type, use_it

  type :: solver_type
    real :: scale = 1.0
  contains
    procedure, private :: solve_variantA
    procedure, private :: solve_variantB
    generic :: solve => solve_variantA, solve_variantB
    procedure :: plain
  end type solver_type

contains

  subroutine solve_variantA(this, x, ierr)
    class(solver_type), intent(in) :: this
    real, intent(inout) :: x(:)
    integer, intent(out) :: ierr
    ierr = 0
    x = this%scale * x
  end subroutine solve_variantA

  subroutine solve_variantB(this, a, x, tol, ierr)
    class(solver_type), intent(in) :: this
    real, intent(in) :: a(:, :)
    real, intent(inout) :: x(:)
    real, intent(in), optional :: tol
    integer, intent(out) :: ierr
    call this%solve_variantA(x, ierr)
    call this%solve(x, ierr)
    x = matmul(a, x)
  end subroutine solve_variantB

  subroutine plain(this)
    class(solver_type), intent(in) :: this
  end subroutine plain

  subroutine use_it(s, a, x, ierr)
    type(solver_type), intent(in) :: s
    real, intent(in) :: a(:, :)
    real, intent(inout) :: x(:)
    integer, intent(out) :: ierr
    call s%solve(x, ierr)
    call s%solve(a, x, ierr=ierr)
    call s%solve(a, x, tol=1.e-3, ierr=ierr)
    call s%solve(x=x, a=a, ierr=ierr)
  end subroutine use_it

end module generic_mod
