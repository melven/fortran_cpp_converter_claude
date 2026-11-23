!> configuration passed to the factories (wrapped class)
module config_mod
  implicit none
  type :: Config_type
    real :: scale = 1.0
  end type Config_type
end module config_mod
