!> pure Fortran: the Fortran implementation registered by hand
program ref_main
  use thing_factory_mod, only: register_thing_factory
  use linear_thing_mod, only: LinearThingFactory_type
  use thing_user_mod, only: use_things
  implicit none
  type(LinearThingFactory_type) :: f
  call register_thing_factory('linear', f)
  call use_things()
end program ref_main
