!> Fortran main with the C++ implementation (registered by its global factory instance)
program mixed_main
  use thing_user_mod, only: use_things
  implicit none
  call use_things()
end program mixed_main
