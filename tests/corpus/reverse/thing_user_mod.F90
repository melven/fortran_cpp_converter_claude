!> Fortran code using things (stays Fortran)
module thing_user_mod
  use config_mod, only: Config_type
  use thing_mod, only: AbstractThing_type
  use thing_factory_mod, only: create_thing_by_name
  implicit none
contains
  subroutine use_things()
    type(Config_type) :: config
    class(AbstractThing_type), allocatable :: thing, other
    class(*), allocatable :: state, kept
    real :: x(3), y(3)
    integer :: ierr
    config%scale = 1.5
    call create_thing_by_name('linear', config, thing, ierr)
    print '(i0)', ierr
    x = [1.0, -2.0, 0.5]
    call thing%evaluate(x, y, ierr)
    print '(i0, 4es24.15)', ierr, y, thing%weight()
    call thing%reset()
    print '(es24.15)', thing%offset()
    config%scale = 4.0
    call create_thing_by_name('linear', config, other, ierr)
    call other%save_state(state, ierr)               ! black box (C++ data in the C++ build)
    call move_alloc(state, kept)
    call thing%restore_state(kept, ierr)
    print '(i0, es24.15)', ierr, thing%weight()      ! 2 * 4.0
    call create_thing_by_name('unknown', config, thing, ierr)
    print '(i0, l2)', ierr, allocated(thing)
  end subroutine use_things
end module thing_user_mod
